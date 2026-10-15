"""报价原文件持久化：PG字节与小引用；任务原文件只向有权接手该任务的人提供。"""
import base64
import hashlib
import uuid
import cpq_auth
import cpq_wf

import cpq_file_policy
MAX_FILE_BYTES=cpq_file_policy.max_bytes()


def validate_file(name,data,*,stored=False):
    name=str(name or '').strip()
    if not name or len(name)>240 or any(c in name for c in ('/','\\','\x00','\r','\n')) or name in ('.','..'):
        raise cpq_wf.WfError('附件名称无效')
    if not isinstance(data,bytes) or not data or len(data)>MAX_FILE_BYTES:
        raise cpq_wf.WfError('附件为空或超过16MB')
    if not stored:
        try:cpq_file_policy.validate(name,len(data))
        except ValueError as exc:raise cpq_wf.WfError(str(exc)) from exc
    return name,hashlib.sha256(data).hexdigest()


def ensure_table(conn):
    cpq_auth._exec(conn,'''CREATE TABLE IF NOT EXISTS cpq_quote_attachment (
        file_id text PRIMARY KEY,owner_id bigint NOT NULL,session_id text NOT NULL DEFAULT '',
        name text NOT NULL,sha256 text NOT NULL,size_bytes bigint NOT NULL,content bytea NOT NULL)''')


def _session_access(session,user):
    card=cpq_wf.get_card(session)
    if not card or not user or (user.get('role_code')!='admin' and str(user.get('user_id')) not in
            {str(card.get('creator_user_id')),str(card.get('current_owner'))}):
        raise cpq_wf.WfError('没有操作该报价附件的权限')


def _ref(row):
    return dict(zip(('file_id','name','sha256','size_bytes'),row))


def save(name,encoded,user,session_id=''):
    if not user:raise cpq_wf.WfError('请先登录')
    if len(str(encoded or ''))>MAX_FILE_BYTES*4//3+8:raise cpq_wf.WfError('附件超过16MB')
    try:data=base64.b64decode(encoded,validate=True)
    except Exception as exc:raise cpq_wf.WfError('文件不是有效base64') from exc
    name,sha=validate_file(name,data)
    if session_id:_session_access(session_id,user)
    conn=cpq_auth._connect()
    try:
        ensure_table(conn)
        row=cpq_auth._exec(conn,'SELECT file_id,name,sha256,size_bytes FROM cpq_quote_attachment WHERE owner_id=%s AND session_id=%s AND name=%s AND sha256=%s',
            (user['user_id'],session_id,name,sha)).fetchone()
        if row:return _ref(row)
        fid=uuid.uuid4().hex
        cpq_auth._exec(conn,'INSERT INTO cpq_quote_attachment (file_id,owner_id,session_id,name,sha256,size_bytes,content) VALUES (%s,%s,%s,%s,%s,%s,%s)',
            (fid,user['user_id'],session_id,name,sha,len(data),data))
        return _ref((fid,name,sha,len(data)))
    finally:conn.close()


def bind(session,refs,user):
    _session_access(session,user)
    if len(refs or [])>20:raise cpq_wf.WfError('单次最多绑定20个附件')
    conn=cpq_auth._connect()
    try:
        ensure_table(conn)
        cpq_auth._exec(conn,'BEGIN')
        for ref in refs or []:
            row=cpq_auth._exec(conn,'SELECT owner_id,session_id FROM cpq_quote_attachment WHERE file_id=%s',(ref['file_id'],)).fetchone()
            if not row or str(row[0])!=str(user['user_id']) or row[1] not in ('',session):
                raise cpq_wf.WfError('附件不属于当前报价，不能绑定')
        for ref in refs or []:
            updated=cpq_auth._exec(conn,"UPDATE cpq_quote_attachment SET session_id=%s WHERE file_id=%s AND owner_id=%s AND session_id IN ('',%s)",
                (session,ref['file_id'],user['user_id'],session))
            if updated.rowcount!=1:raise cpq_wf.WfError('附件已被其他报价绑定，请重试')
        cpq_auth._exec(conn,'COMMIT')
    except Exception:
        try:cpq_auth._exec(conn,'ROLLBACK')
        except Exception:pass
        raise
    finally:conn.close()
    return list_session(session,user)


def list_session(session,user):
    _session_access(session,user)
    conn=cpq_auth._connect()
    try:
        ensure_table(conn)
        return [_ref(r) for r in cpq_auth._exec(conn,'SELECT file_id,name,sha256,size_bytes FROM cpq_quote_attachment WHERE session_id=%s ORDER BY file_id',(session,)).fetchall()]
    finally:conn.close()


def task_files(task,user):
    """task必须来自已鉴权task_detail；引用仍核对来源报价，不信任客户端路径或字节。"""
    refs=(task.get('payload') or {}).get('attachment_refs') or []
    if len(refs)>20:raise cpq_wf.WfError('单次最多承接20个附件')
    conn=cpq_auth._connect()
    try:
        ensure_table(conn);result=[]
        for ref in refs:
            row=cpq_auth._exec(conn,'SELECT name,sha256,size_bytes,content,session_id FROM cpq_quote_attachment WHERE file_id=%s',(ref.get('file_id'),)).fetchone()
            if not row or row[4]!=task.get('session_id'):raise cpq_wf.WfError('任务附件与报价不一致')
            # 新上传白名单不追溯封禁已存原件；权限、名称、大小和SHA仍全量校验。
            data=bytes(row[3]);name,sha=validate_file(row[0],data,stored=True)
            if sha!=row[1] or len(data)!=row[2]:raise cpq_wf.WfError('附件完整性校验失败')
            result.append({'name':name,'sha256':sha,'data':base64.b64encode(data).decode()})
        return result
    finally:conn.close()


def read_file(session,file_id,user):
    _session_access(session,user)
    refs=list_session(session,user)
    if file_id not in {r['file_id'] for r in refs}:raise cpq_wf.WfError('附件不属于该报价')
    return task_files({'session_id':session,'payload':{'attachment_refs':[{'file_id':file_id}]}},user)[0]

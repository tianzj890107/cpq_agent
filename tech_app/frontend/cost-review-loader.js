/* A new load supersedes both requests; failed verification never removes saved data. */
(function(root){
  'use strict';
  root.CPQCostReviewLoader={create(options){
    let sequence=0,controller=null;
    return {
      async load(){
        const ticket=++sequence;
        if(controller)controller.abort();
        controller=new AbortController();
        const signal=controller.signal;
        try {
          const saved=await options.readSaved(signal);
          if(ticket!==sequence)return {ok:false,error:{code:'superseded',message:'已由新的读取请求接替'}};
          options.render(saved);
          if(saved?.verification_pending===false)return {ok:true};
          void Promise.resolve().then(()=>options.readVerified(signal)).then(data=>{
            if(ticket===sequence)options.render(data);
          }).catch(error=>{if(ticket===sequence)options.failed(error);});
          return {ok:true};
        } catch(error){
          if(ticket!==sequence)return {ok:false,error:{code:'superseded',message:'已由新的读取请求接替'}};
          options.failed(error);
          // Saved endpoint can fail independently. Try the authoritative exit once,
          // without allowing a superseded response to replace another project's data.
          try {
            const data=await options.readVerified(signal);
            if(ticket!==sequence)return {ok:false,error:{code:'superseded',message:'已由新的读取请求接替'}};
            options.render(data);
            return {ok:true};
          } catch(verifiedError) {
            if(ticket===sequence)options.failed(verifiedError);
            return {ok:false,error:{code:'cost-read-failed',message:verifiedError.message}};
          }
        }
      },
      invalidate(){++sequence;if(controller)controller.abort();}
    };
  }};
})(typeof window==='undefined'?globalThis:window);

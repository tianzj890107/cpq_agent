"""候选置信度与默认预选：仅前端预览，不代替人工确权。"""
from __future__ import annotations

import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "tech_app/frontend/app.js"


def run_helpers(js: str) -> str:
    source = APP.read_text(encoding="utf-8")
    names = ("packagingCandidateConfidence", "packagingRankedCandidates",
             "packagingCandidateSelectionIndex")
    functions = []
    for name in names:
        start = source.index("function " + name + "(")
        end = source.index("\n}", start) + 2
        functions.append(source[start:end])
    result = subprocess.run(["node", "-e", "\n".join(functions) + "\n" + js],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class CandidateConfidenceTest(unittest.TestCase):
    def test_evidence_ranking_is_stable_and_bounded(self):
        out = run_helpers("""
const weak={id:'z',entity_ids:['e1'],component_ids:[],bbox:[0,0,100,100],
  distance_mm:150,anchor_in_region:false,dimension_spatial:false,geometry_status:'unknown'};
const strong={id:'a',entity_ids:['e2'],component_ids:['c2'],bbox:[0,0,100,100],
  distance_mm:1,anchor_in_region:true,dimension_spatial:true,geometry_status:'supported'};
const ranked=packagingRankedCandidates([weak,strong]);
if (ranked[0].candidate.id!=='a' || ranked[1].candidate.id!=='z') process.exit(2);
if (!(ranked[0].confidence>ranked[1].confidence && ranked[0].confidence<=85)) process.exit(3);
const reverse=packagingRankedCandidates([strong,weak]);
if (reverse[0].candidate.id!=='a') process.exit(4);
const eight=Array.from({length:8},(_,i)=>({...weak,id:'candidate-'+i,distance_mm:120-i*10}));
const eightRanked=packagingRankedCandidates(eight);
if (eightRanked.length!==8 || eightRanked.some((row,i)=>!Number.isInteger(row.confidence)
  || (i>0 && row.confidence>eightRanked[i-1].confidence))) process.exit(13);
if (packagingCandidateConfidence({id:'x',bbox:[0,0,0,0],distance_mm:'nan'})>85) process.exit(5);
if (packagingCandidateConfidence({id:'bbox-only',bbox:[0,0,100,100],distance_mm:0,
  anchor_in_region:true,dimension_spatial:true,geometry_status:'supported'})!==0) process.exit(11);
const page={id:'page',entity_ids:['e3'],bbox:[0,0,5000,2000],distance_mm:300,
  anchor_in_region:false,dimension_spatial:false,geometry_status:'supported'};
const pageClose={...page,distance_mm:0};
if (!(packagingCandidateConfidence(page)<packagingCandidateConfidence(pageClose))) process.exit(12);
if (packagingCandidateSelectionIndex([weak,strong],'',undefined)!==1) process.exit(6);
if (packagingCandidateSelectionIndex([weak,strong],'z',undefined)!==0) process.exit(7);
if (packagingCandidateSelectionIndex([weak,strong],'missing',undefined)!==1) process.exit(8);
if (packagingCandidateSelectionIndex([weak,strong],'',0)!==0) process.exit(9);
if (packagingCandidateSelectionIndex([], '', undefined)!==-1) process.exit(10);
process.stdout.write('ok');
""")
        self.assertEqual("ok", out)

    def test_options_show_percent_and_default_uses_ranked_best(self):
        source = APP.read_text(encoding="utf-8")
        body = source.split("function openPackagingBusinessPart(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("packagingRankedCandidates(candidates)", body)
        self.assertIn("packagingCandidateSelectionIndex(candidates, manualId, requestedCandidateIndex)", body)
        self.assertIn("confidence}%", body)
        self.assertIn("置信度", body)
        self.assertIn("packagingManualCandidateSelection", body)

    def test_candidate_change_writes_binding_without_second_confirmation(self):
        source = APP.read_text(encoding="utf-8")
        body = source.split("function openPackagingBusinessPart(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('selector.addEventListener("change", async', body)
        self.assertIn("fetch(packagingGeometryBindingUrl(wanted)", body)
        self.assertIn("packagingManualCandidateSelection.set", body)
        self.assertNotIn('id="packagingConfirmCandidate"', body)


if __name__ == "__main__":
    unittest.main()

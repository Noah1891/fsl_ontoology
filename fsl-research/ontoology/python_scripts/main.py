#import generate_resources
import merge_fsl
import convert_to_owl
import send_oops_request
import build_batch_request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MAIN_DIR = SCRIPT_DIR / ".." / ".." / ".." / "ontologies"
MODULES = ["ae.ttl", "ce.ttl", "fe.ttl", "ie.ttl", "le.ttl", "pe.ttl", "tbox.ttl", "te.ttl"]
MERGED_DIR = SCRIPT_DIR / ".." / "merged" / "fsl"
FSL_MERGED_TTL = MERGED_DIR / "fsl_merged.ttl"
FSL_MERGED_OWL = MERGED_DIR / "fsl_merged.owl"
OOPS_RESPONSE = SCRIPT_DIR / ".." / "oops_prompting" / "report"
LLM_PROMPTING = SCRIPT_DIR / ".." / "llm_prompting"

def main():
    module_paths = [MAIN_DIR / m for m in MODULES]
    merged_str = merge_fsl.merge(MAIN_DIR / "fsl.ttl", module_paths)
    FSL_MERGED_TTL.write_text(merged_str, encoding='utf-8')
    convert_to_owl.convert_merged_to_owl(FSL_MERGED_TTL, FSL_MERGED_OWL)
    send_oops_request.scan_ontology(FSL_MERGED_OWL, OOPS_RESPONSE / "oops_report.xml")
    reqs = build_batch_request.build_batch_requests(
            merged_ontology_path=FSL_MERGED_TTL,
            oops_xml_path=str(OOPS_RESPONSE / "oops_report.xml"),
            pitfall_ids=[i for i in range(1, 42)],
            fsl_summary_path=LLM_PROMPTING / "system_message/FSLsummary.md"
    )
    build_batch_request.write_batch_file(reqs, str(LLM_PROMPTING / "batches/batch_input"))


if __name__ == '__main__':
    main()
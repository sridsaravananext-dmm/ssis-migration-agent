"""Backend evidence/review CLI; workload deployment remains separate."""
import argparse
import json
from pathlib import Path
from ssis_migration.review_store import ReviewStore,revalidate_notebook


def write(path,value):
    import tempfile
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=p.parent,prefix=p.name+'.',delete=False) as file:
        file.write(json.dumps(value,indent=2,ensure_ascii=False));temporary=Path(file.name)
    try:temporary.replace(p)
    finally:temporary.unlink(missing_ok=True)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    review=commands.add_parser('review',help='Record version-scoped evidence; does not remove execution blockers')
    review.add_argument('--package-result',required=True);review.add_argument('--store',required=True)
    review.add_argument('--finding',required=True);review.add_argument('--decision',required=True,choices=['OPEN','NEEDS_IMPLEMENTATION','ACCEPTED_WARNING','EVIDENCE_RECORDED'])
    review.add_argument('--author',required=True);review.add_argument('--note',required=True);review.add_argument('--evidence',action='append',default=[])
    review.add_argument('--expected-revision',type=int,default=0)
    history=commands.add_parser('review-history');history.add_argument('--store',required=True);history.add_argument('--version',required=True)
    validate=commands.add_parser('revalidate');validate.add_argument('--notebook',required=True);validate.add_argument('--package-result',required=True);validate.add_argument('--output',required=True)
    compare=commands.add_parser('compare',help='Compare authorized stable Spark table snapshots')
    compare.add_argument('--source-table',required=True);compare.add_argument('--target-table',required=True);compare.add_argument('--keys',nargs='*',default=[])
    compare.add_argument('--absolute-tolerances',help='JSON file mapping numeric columns to absolute tolerances');compare.add_argument('--output',required=True)
    inspect=commands.add_parser('inspect-targets',help='Read target table metadata; never changes mappings or schemas')
    inspect.add_argument('--plan',required=True);inspect.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    if args.command=='review-history':result=ReviewStore(args.store).history(args.version)
    elif args.command in {'compare','inspect-targets'}:
        from pyspark.sql import SparkSession
        spark=SparkSession.getActiveSession() or SparkSession.builder.appName('ssis-dataset-verification').getOrCreate()
        if args.command=='inspect-targets':
            from ssis_migration.target_inspection import inspect_targets
            if Path(args.output).resolve()==Path(args.plan).resolve():raise ValueError('Output cannot overwrite the input plan')
            result=inspect_targets(spark,json.loads(Path(args.plan).read_text()))
        else:
            from ssis_migration.verification import compare_frames
            tolerances=json.loads(Path(args.absolute_tolerances).read_text()) if args.absolute_tolerances else None
            result=compare_frames(spark.table(args.source_table),spark.table(args.target_table),keys=args.keys,absolute_tolerances=tolerances)
        write(args.output,result)
    else:
        entry=json.loads(Path(args.package_result).read_text());findings=entry.get('findings',[])
        if args.command=='review':
            manifest=json.loads(Path(entry['artifacts']['manifest']).read_text());matches=[f for f in findings if f['finding_id']==args.finding]
            if len(matches)!=1:raise ValueError('Finding ID must exist uniquely in the supplied package result')
            result=ReviewStore(args.store).record(manifest['fingerprint'],matches[0],decision=args.decision,author=args.author,note=args.note,
                evidence=args.evidence,expected_revision=args.expected_revision)
        else:
            if 'findings' not in entry:raise ValueError('Use the complete package_result.json to retain source blockers')
            if Path(args.output).resolve() in {Path(args.notebook).resolve(),Path(args.package_result).resolve()}:
                raise ValueError('Revalidation output cannot overwrite notebook or package evidence')
            result=revalidate_notebook(args.notebook,findings=findings);write(args.output,result)
    print(json.dumps(result,indent=2,ensure_ascii=False))
    if isinstance(result,dict) and result.get('status') in {'MISMATCH','INVALID_COMPARISON','REVALIDATION_REQUIRED'}:return 1
    return 0


if __name__=='__main__':raise SystemExit(main())

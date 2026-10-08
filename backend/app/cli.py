import argparse
import json
from app.core import clean

def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['import','audit','analyze','train','all','groq-check']);args=parser.parse_args()
    from app.services.ingestion import import_dataset,audit_directory
    from app.core import settings
    if args.command=='audit':result=audit_directory(settings.data_dir/'synthetic')[1]
    elif args.command=='import':result=import_dataset()
    elif args.command=='groq-check':
        from app.services.briefs import check_groq
        result=check_groq()
    else:
        from app.services.pipeline import analyze
        if args.command=='all':import_dataset()
        result=analyze()
    print(json.dumps(clean(result),indent=2))

if __name__=='__main__':main()

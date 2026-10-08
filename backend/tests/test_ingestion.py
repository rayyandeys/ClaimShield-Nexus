import csv
import shutil
from pathlib import Path
import pytest
from app.core import settings
from app.services.ingestion import audit_directory

@pytest.fixture
def snapshot(tmp_path):
    path=tmp_path/'snapshot';shutil.copytree(settings.data_dir/'synthetic',path);return path

def mutate(path,table,field,value):
    file=path/f'{table}.csv'
    with file.open(newline='') as f:reader=csv.DictReader(f);columns=reader.fieldnames;rows=list(reader)
    rows[0][field]=value
    with file.open('w',newline='') as f:writer=csv.DictWriter(f,fieldnames=columns);writer.writeheader();writer.writerows(rows)

@pytest.mark.parametrize('table,field,value',[
    ('claims','claim_id',''),('claims','member_id','DOES_NOT_EXIST'),('claims','billed_amount_usd','-3'),('claims','service_start','invalid-time'),('supply_items','quantity','-4'),('relationships','source_id','UNKNOWN'),('claims','correction_of_claim_id','UNKNOWN'),('claims','paid_amount_usd','99999999'),('claim_estimates','difference_usd','99999999')])
def test_invalid_source_rejected(snapshot,table,field,value):
    mutate(snapshot,table,field,value);_,report=audit_directory(snapshot)
    assert report['status']=='REJECTED'

def test_missing_reference_file(snapshot):
    (snapshot/'providers.csv').unlink();_,report=audit_directory(snapshot)
    assert report['status']=='REJECTED' and any('missing' in i['message'] for i in report['issues'])

def test_duplicate_primary_key(snapshot):
    path=snapshot/'members.csv';lines=path.read_text().splitlines();path.write_text('\n'.join(lines+[lines[1]])+'\n')
    _,report=audit_directory(snapshot);assert report['status']=='REJECTED'

def test_original_snapshot_counts_and_groundtruth_excluded():
    data,report=audit_directory(settings.data_dir/'synthetic')
    assert report['status']=='PASS_WITH_WARNINGS' and len(data['claims'])==20000
    assert 'ground_truth' not in data
    assert not any('scenario_type' in r for r in data['claims'])

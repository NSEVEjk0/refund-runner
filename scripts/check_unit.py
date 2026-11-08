#!/usr/bin/env python3
"""Run the repository's local contract checks and write measured JSON results."""
import argparse
import json
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--output',default=str(ROOT/'.local/unit-checks.json'))
    args=parser.parse_args(); names=[]
    class Result(unittest.TextTestResult):
        def addSuccess(self,test): super().addSuccess(test); names.append({'name':test.id(),'passed':True})
        def addFailure(self,test,error): super().addFailure(test,error); names.append({'name':test.id(),'passed':False})
        def addError(self,test,error): super().addError(test,error); names.append({'name':test.id(),'passed':False})
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'))
    result=unittest.TextTestRunner(verbosity=1,resultclass=Result).run(suite)
    report={'status':'complete' if result.wasSuccessful() else 'failed','measured_at':time.time(),
            'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
            'skipped':len(result.skipped),'checks':names,'scope':'Local fixed accounting, approval, privacy and orchestration checks. Provider and sandbox test doubles are not live containment evidence.'}
    path=Path(args.output); path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(report,indent=2)+'\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
if __name__=='__main__':main()

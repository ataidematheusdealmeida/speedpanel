"""One measurement per subprocess, killed as a group on the configured timeout."""
import os
import sys
import json
os.environ['DISABLE_WORKER']='1'
import app
try:
    print(json.dumps(app.measure(json.load(sys.stdin))))
except Exception as error:
    message=str(error).split('Call log:')[0].strip()
    print(json.dumps({'error':message[-1000:]}))

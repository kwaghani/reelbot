"""Export retained, unmodified XCTest screenshots under stable names."""
import json,subprocess,sys,tempfile,shutil
from pathlib import Path
result,out=sys.argv[1:3]
output=Path(out);output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='reelbot-ui-export-') as tmp:
 subprocess.run(['xcrun','xcresulttool','export','attachments','--path',result,'--output-path',tmp],check=True,stdout=subprocess.DEVNULL)
 manifest=json.load(open(Path(tmp)/'manifest.json'))
 for test in manifest:
  for item in test['attachments']:
   name=item['suggestedHumanReadableName'];file=item['exportedFileName']
   if file.endswith('.png') and '_0_' in name:
    shutil.copy2(Path(tmp)/file,output/(name.split('_0_')[0]+'.png'))
 (output/'manifest.json').write_text(json.dumps(manifest,indent=2))
print('Exported',len(list(output.glob('*.png'))),'screenshots to',output)

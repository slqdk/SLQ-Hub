// The oracle for backend/app/workflows/convert.py (0.67.0): ComfyUI's own editor turns workflows in the editor's
// format into the API format, run headless. Use it when COMFYUI_REF changes (the editor's format may change too).
//
//   1. Run that ComfyUI on the CPU somewhere (it needs no GPU for this):
//        git clone --depth 1 --branch <COMFYUI_REF> https://github.com/comfyanonymous/ComfyUI && cd ComfyUI
//        python3 -m venv v && v/bin/pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
//        v/bin/pip install -r requirements.txt   (its torch lines aside)
//        v/bin/python main.py --cpu --listen 127.0.0.1 --port 8199 --disable-partner-nodes
//      Its workflow templates come with it (comfyui-workflow-templates-json in its requirements).
//   2. Put the editor-format workflows to check in one folder (*.json), then, with Playwright installed:
//        node convert.js <folder> <out folder> [http://127.0.0.1:8199]
//   3. python3 compare.py <folder> <out folder> <object_info.json>   (curl .../object_info > object_info.json)
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

(async () => {
  const [inDir, outDir, url = 'http://127.0.0.1:8199'] = process.argv.slice(2);
  fs.mkdirSync(outDir, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto(url + '/', { waitUntil: 'load' });
  await page.waitForFunction(() => window.app && window.app.graph && window.app.graphToPrompt, null, { timeout: 120000 });
  await page.waitForTimeout(3000);
  let ok = 0, bad = 0;
  for (const f of fs.readdirSync(inDir).filter((n) => n.endsWith('.json'))) {
    const wf = JSON.parse(fs.readFileSync(path.join(inDir, f), 'utf8'));
    try {
      const out = await page.evaluate(async (w) => {
        await window.app.loadGraphData(w, true, false);
        return (await window.app.graphToPrompt()).output;
      }, wf);
      fs.writeFileSync(path.join(outDir, f), JSON.stringify(out, null, 1));
      ok++;
    } catch (e) {
      console.error('FAIL', f, String(e).slice(0, 300));
      bad++;
    }
  }
  console.log(`converted ${ok}, failed ${bad}`);
  await browser.close();
})();

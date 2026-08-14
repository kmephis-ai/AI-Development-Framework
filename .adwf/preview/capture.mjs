import fs from 'node:fs';
import { chromium } from 'playwright';
const req=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const browser=await chromium.launch({headless:true});
const browserVersion=browser.version();
const screenshots=[]; const consoleErrors=[]; const failedRequests=[];
async function capture(label,url,viewport){
  const context=await browser.newContext({viewportSize:viewport,deviceScaleFactor:1,reducedMotion:'reduce'});
  const page=await context.newPage();
  page.on('console',m=>{if(m.type()==='error') consoleErrors.push({label,text:m.text().slice(0,500)});});
  page.on('requestfailed',r=>failedRequests.push({label,url:r.url().slice(0,500),error:r.failure()?.errorText||'UNKNOWN'}));
  const response=await page.goto(url,{waitUntil:'networkidle',timeout:30000});
  if(!response || response.status()>=400) throw new Error(`HTTP_${response?.status()||'NO_RESPONSE'}`);
  await page.addStyleTag({content:'*,*::before,*::after{animation-duration:0s!important;transition-duration:0s!important;caret-color:transparent!important}'});
  const path=`${req.output_dir}/${label}.png`; await page.screenshot({path,fullPage:true}); screenshots.push({name:label,path});
  const accessibility=await page.evaluate(()=>({
    title_present:document.title.trim().length>0,
    images_without_alt:[...document.images].filter(i=>!i.hasAttribute('alt')).length,
    unlabeled_controls:[...document.querySelectorAll('button,input,select,textarea')].filter(el=>{
      if(el.getAttribute('aria-label')||el.getAttribute('aria-labelledby')) return false;
      if(el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)) return false;
      return el.tagName!=='BUTTON' || !el.textContent.trim();
    }).length,
    main_landmark:!!document.querySelector('main,[role="main"]')
  }));
  await context.close(); return accessibility;
}
let a11y={};
for(const [name,vp] of Object.entries(req.viewports)){
  const cur=await capture(name,req.url,vp); a11y[name]=cur;
  if(req.baseline_url) await capture(`baseline-${name}`,req.baseline_url,vp);
}
await browser.close();
const hardIssues=Object.values(a11y).reduce((n,v)=>n+(v.images_without_alt||0)+(v.unlabeled_controls||0)+(v.title_present?0:1),0);
fs.writeFileSync(`${req.output_dir}/capture-result.json`,JSON.stringify({screenshots,console_errors:consoleErrors,failed_requests:failedRequests,browser_version:browserVersion,node_version:process.version,platform:process.platform,arch:process.arch,accessibility:{status:hardIssues===0?'BASIC_PASS':'BASIC_WARN',issues:hardIssues,details:a11y}},null,2));

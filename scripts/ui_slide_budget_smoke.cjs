// UI-only regression: all API calls intercepted, no production jobs created.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');

(async () => {
  const out=path.resolve(__dirname,'../test-results/slide-budget-ui');
  await fs.mkdir(out,{recursive:true});
  const browser=await chromium.launch({headless:true, executablePath:process.env.STUDIO_TEST_BROWSER});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  let blocked=false, generateBody=null;
  const message='Запрошено 4 слайда. Подготовлен план на 6 слайдов; исходное содержание сохранено.';
  function ready() {
    return {id:'budget-test',state:'ready',template:{name:'Проверка бюджета',colors:['#154A67'],
      slide_count:3,patterns:[{}],layout_count:1,font:'Play',font_origin:{kind:'local'}},
      content:{facts:4,tables:4},constraints:{slides:4},
      analysis:{planned_slides:blocked?null:6,planning_status:blocked?'needs_input':'completed',
        template_semantics:{status:'partial'},document_structure:{status:'degraded'},
        planning_source:'model',slide_budget:{status:blocked?'needs_input':'adjusted',planned:blocked?null:6},
        composition_preview:{verified:!blocked}},
      diagnostics:[{severity:'warning',message:blocked?'Нужно 33 слайда. Анализ сохранён; сократите материал.':message}]};
  }
  await page.route('**/api/**',async route=>{
    const url=new URL(route.request().url());let result;
    if(url.pathname==='/api/health')result={model_mode:'extractive',features:{}};
    else if(url.pathname==='/api/references')result=[];
    else if(url.pathname==='/api/runtime')result={restart_required:false,organizer_preanalysis:false};
    else if(url.pathname==='/api/prepare')result={id:'budget-test'};
    else if(url.pathname==='/api/jobs/budget-test')result=ready();
    else if(url.pathname==='/api/generate'){generateBody=route.request().postDataJSON();result={id:'generation-test'};}
    else if(url.pathname==='/api/jobs/generation-test')result={state:'failed',error:'Контрольный ответ без генерации'};
    else throw Error('Unexpected API call: '+url.pathname);
    await route.fulfill({json:result});
  });
  try {
    await page.goto(process.env.STUDIO_UI_TEST_URL || 'http://127.0.0.1:8765');
    await page.locator('#template').setInputFiles({name:'test.pptx',mimeType:'application/octet-stream',buffer:Buffer.from('API mocked')});
    await page.locator('#content').fill('Тестовое содержание.');
    await page.locator('#slides').selectOption('mini');
    await page.locator('#prepare-button').click();
    await page.waitForFunction(()=>!document.querySelector('#generate-button').disabled);
    assert.match(await page.locator('#prep-state').textContent(),/Есть замечания/);
    assert.match(await page.locator('#profile').textContent(),/Запрошено 4/);
    assert.match(await page.locator('#profile').textContent(),/непроверенные макеты исключены/);
    assert.match(await page.locator('#profile').textContent(),/остальные блоки сохранены без сокращения/);
    assert.match(await page.locator('#generate-button').textContent(),/по 6 слайдов/);
    assert.equal(generateBody,null);
    await page.screenshot({path:path.join(out,'warning.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
    await page.screenshot({path:path.join(out,'mobile-warning.png'),fullPage:true});
    await page.setViewportSize({width:1440,height:1000});
    await page.locator('#generate-button').click();
    await page.waitForFunction(()=>!document.querySelector('#prepare-button').disabled);
    assert.equal(generateBody.accept_adjusted_slide_count,true);
    blocked=true;
    await page.locator('#prepare-button').click();
    await page.waitForFunction(()=>!document.querySelector('#prepare-button').disabled);
    assert.match(await page.locator('#profile').textContent(),/Анализ сохранён/);
    assert.equal(await page.locator('#generate-button').isDisabled(),true);
    assert.equal(await page.locator('#prep-loader').isVisible(),false);
    await page.screenshot({path:path.join(out,'over-cap.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,pageErrors:errors}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});

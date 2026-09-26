// Run against an isolated extractive-mode server, never a production workspace.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');

(async () => {
  const root = path.resolve(__dirname,'..');
  const out = path.join(root,'test-results/ui');
  await fs.mkdir(out,{recursive:true});
  const browser = await chromium.launch({headless:true,...(process.env.STUDIO_TEST_BROWSER ? {executablePath:process.env.STUDIO_TEST_BROWSER} : {})});
  const page = await browser.newPage({viewport:{width:1440,height:1050}});
  const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  async function checkLoader(endpoint, selector, action, screenshot) {
    let release, arrived, continued;
    const gate = new Promise(resolve=>release=resolve);
    const requested = new Promise(resolve=>arrived=resolve);
    const handled = new Promise(resolve=>continued=resolve);
    const handler = async route=>{arrived();await gate;try {await route.continue();} finally {continued();}};
    await page.route(endpoint,handler);
    try {
      await action(); await requested;
      assert.equal(await page.locator(selector).isVisible(),true);
      assert.equal(await page.locator('#toast').isVisible(),false);
      assert.equal(await page.locator('#result-timings').isVisible(),false);
      assert.equal(await page.locator('.progress-track, #timer').count(),0);
      assert.notEqual(await page.locator(selector+' .loader-card').first().evaluate(n=>getComputedStyle(n).animationName),'none');
      await page.screenshot({path:path.join(out,screenshot),fullPage:true});
      await page.emulateMedia({reducedMotion:'reduce'});
      assert.equal(await page.locator(selector+' .loader-card').first().evaluate(n=>getComputedStyle(n).animationName),'none');
      await page.emulateMedia({reducedMotion:'no-preference'});
    } finally {release();await handled;await page.unroute(endpoint,handler);}
  }
  try {
    await page.goto(process.env.STUDIO_UI_TEST_URL || 'http://127.0.0.1:8766');
    await page.waitForFunction(()=>document.querySelector('#model-mode').textContent.includes('Автономно'));
    assert.equal(await page.locator('#reference').inputValue(),'');
    assert.equal(await page.locator('#generate-button').isDisabled(),true);
    await page.locator('#content').fill('# Проверка\n## Слайд 1. Контекст\nКоманда работает с заявками.\n## Слайд 2. Процесс\nЗаявки поступают через единый интерфейс.\n## Слайд 3. Результат\nСтатус заявки доступен сотрудникам.');
    await page.locator('#slides').fill('3');
    await page.locator('#prepare-button').click();
    assert.match(await page.locator('#toast').textContent(),/Выберите шаблон/);
    assert.match(await page.locator('#template').getAttribute('accept'),/\.potx/);
    await page.locator('#template').setInputFiles(process.env.STUDIO_TEST_TEMPLATE || path.join(out,'unknown.pptx'));
    const picture=path.join(out,'upload-fixture.png');
    await page.screenshot({path:picture});
    await page.locator('#images').setInputFiles(picture);
    assert.match(await page.locator('#images-list').textContent(),/upload-fixture.png/);
    await page.route('**/api/prepare',route=>route.fulfill({json:{id:'ui-fonts'}}));
    await page.route('**/api/jobs/ui-fonts',route=>route.fulfill({json:{id:'ui-fonts',state:'waiting_fonts',error:'Добавьте TTF',missing_fonts:[{requested:'Corporate Font <img>',reason:'Не найден'}]}}));
    await page.locator('#prepare-button').click();
    await page.waitForFunction(()=>document.querySelector('#prep-state').textContent==='Нужны шрифты');
    assert.equal(await page.locator('#generate-button').isDisabled(),true);
    assert.match(await page.locator('#profile').textContent(),/Corporate Font <img>/);
    assert.equal(await page.locator('#profile img').count(),0);
    await page.screenshot({path:path.join(out,'missing-fonts.png'),fullPage:true});
    await page.route('**/api/packages/ui-fonts/retry-fonts',route=>route.fulfill({json:{id:'ui-font-retry'}}));
    await page.route('**/api/jobs/ui-font-retry',route=>route.fulfill({json:{id:'ui-font-retry',state:'failed',error:'Повторная проверка выполнена'}}));
    await page.getByRole('button',{name:'Проверить шрифты повторно',exact:true}).click();
    await page.waitForFunction(()=>!document.querySelector('#prepare-button').disabled);
    assert.match(await page.locator('#toast').textContent(),/Повторная проверка выполнена/);
    await page.unroute('**/api/prepare'); await page.unroute('**/api/jobs/ui-fonts');
    await page.unroute('**/api/packages/ui-fonts/retry-fonts'); await page.unroute('**/api/jobs/ui-font-retry');
    // Real server phases are translated, not simulated using a UI timer.
    await page.route('**/api/prepare',route=>route.fulfill({json:{id:'ui-analysis-phase'}}));
    let analysisPolls=0;
    await page.route('**/api/jobs/ui-analysis-phase',route=>route.fulfill({json:++analysisPolls===1
      ? {id:'ui-analysis-phase',state:'running',phase:'Смысловой анализ макетов по тексту и геометрии'}
      : {id:'ui-analysis-phase',state:'failed',error:'Тестовый отказ анализа'}}));
    await page.locator('#prepare-button').click();
    await page.waitForFunction(()=>document.querySelector('#prep-phase').textContent.includes('для какого содержания'));
    assert.equal(await page.locator('#prep-loader').isVisible(),true);
    await page.waitForFunction(()=>!document.querySelector('#prepare-button').disabled);
    await page.unroute('**/api/prepare'); await page.unroute('**/api/jobs/ui-analysis-phase');
    await page.screenshot({path:path.join(out,'desktop-input.png'),fullPage:true});
    await checkLoader('**/api/prepare','#prep-loader',()=>page.locator('#prepare-button').click(),'analysis-loader.png');
    await page.waitForFunction(()=>!document.querySelector('#generate-button').disabled,{},{timeout:90000});
    assert.match(await page.locator('#profile').textContent(),/Технический разбор/);
    assert.match(await page.locator('#profile').textContent(),/1 картинок/);
    assert.equal(await page.locator('#profile .notice.warning').count(),0);
    assert.doesNotMatch(await page.locator('#profile').textContent(),/заняла|сек\./);
    assert.equal(await page.locator('#prep-loader').isVisible(),false);
    assert.equal(await page.locator('#result-timings').isVisible(),false);
    // A backend failure must stop the loader, remain visible and allow retry.
    await page.route('**/api/generate',route=>route.fulfill({json:{id:'ui-timeout'}}));
    let generationPolls=0;
    await page.route('**/api/jobs/ui-timeout',route=>route.fulfill({json:++generationPolls===1
      ? {id:'ui-timeout',state:'running',phase:'DeepPresenter: выбор и проверка композиций'}
      : {id:'ui-timeout',state:'failed',error:'DeepPresenter: тестовый таймаут модели'}}));
    await page.locator('#generate-button').click();
    await page.waitForFunction(()=>document.querySelector('#generation-phase').textContent.includes('Подбираем оформление'));
    assert.equal(await page.locator('#generation-status').isVisible(),true);
    await page.waitForFunction(()=>!!document.querySelector('#generation-error') && !document.querySelector('#generate-button').disabled);
    assert.match(await page.locator('#generation-error').textContent(),/DeepPresenter/);
    assert.equal(await page.locator('#generation-status').isVisible(),false);
    assert.equal(await page.locator('#results').isVisible(),false);
    await page.unroute('**/api/generate'); await page.unroute('**/api/jobs/ui-timeout');
    await checkLoader('**/api/generate','#generation-status',()=>page.locator('#generate-button').click(),'generation-loader.png');
    await page.waitForFunction(()=>!document.querySelector('#results').hidden && !document.querySelector('#prepare-button').disabled,{},{timeout:90000});
    assert.equal(await page.locator('.result-card').count(),3);
    assert.equal(await page.locator('#generation-status').isVisible(),false);
    assert.equal(await page.locator('#generation-error').count(),0);
    assert.equal(await page.locator('#toast').isVisible(),false);
    assert.equal(await page.locator('#result-timings dd').count(),2);
    assert.doesNotMatch(await page.locator('#result-timings').textContent(),/Нет данных/);
    assert.match(await page.locator('#audit-list').textContent(),/Различия геометрии композиций: 3 из 3/);
    const firstResultURL=await page.locator('#manifest-link').getAttribute('href');
    const firstManifest=await (await page.request.get(new URL(firstResultURL,page.url()).href)).json();
    const generationId=firstResultURL.split('/')[3];
    const generationJob=await (await page.request.get(new URL('/api/jobs/'+generationId,page.url()).href)).json();
    assert.equal(generationJob.analysis_seconds,firstManifest.input_manifest.analysis_seconds);
    // Legacy history results resolve preparation time without mixing in editor state.
    await page.route('**/api/jobs',async route=>{
      const response=await route.fetch(); const jobs=await response.json();
      for (const job of jobs) delete job.analysis_seconds;
      await route.fulfill({json:jobs});
    });
    await page.locator('.preview-button').first().click();
    assert.equal(await page.locator('#preview-dialog').evaluate(node=>node.open),true);
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#slide-counter').textContent(),'2 / 3');
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#preview-dialog').evaluate(node=>node.open),false);
    await page.screenshot({path:path.join(out,'desktop-result.png'),fullPage:true});
    await page.locator('#content').fill('# Другой ввод\nНовый исходный факт.');
    assert.equal(await page.locator('#generate-button').isDisabled(),true);
    assert.equal(await page.locator('#prep-state').textContent(),'Нужен новый анализ');
    await page.locator('#nav-history').click();
    await page.getByRole('button',{name:'Открыть',exact:true}).first().click();
    await page.waitForFunction(()=>!document.querySelector('#result-timings').textContent.includes('Нет данных'));
    assert.equal(await page.locator('#generate-button').isDisabled(),true,'History result must not reactivate a stale preparation');
    let revisedPackage=null;
    page.on('request',request=>{
      const match=request.url().match(/\/api\/packages\/([^/]+)\/revise$/);
      if(match) revisedPackage=match[1];
    });
    await page.locator('#revision').fill('Сделай не более 2 слайдов');
    await page.locator('#revise-form button').click();
    await page.waitForFunction(()=>document.querySelector('#content').disabled);
    await page.waitForFunction(()=>!document.querySelector('#prepare-button').disabled,{},{timeout:90000});
    assert.equal(revisedPackage,firstManifest.package_id);
    assert.match(await page.locator('.result-meta').first().textContent(),/^2 слайдов/);
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(()=>window.scrollTo(0,0));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true,'No horizontal scroll');
    assert.equal(await page.locator('#nav-history').isVisible(),true);
    await page.screenshot({path:path.join(out,'mobile.png'),fullPage:true});
    // A rejected request must unlock all controls and offer a retry.
    await page.route('**/api/prepare',route=>route.fulfill({status:422,contentType:'application/json',body:JSON.stringify({detail:'Тестовая ошибка загрузки'})}));
    await page.locator('#prepare-button').click();
    await page.waitForFunction(()=>!document.querySelector('#prepare-button').disabled);
    assert.match(await page.locator('#toast').textContent(),/Тестовая ошибка загрузки/);
    assert.equal(await page.locator('#content').isDisabled(),false);
    assert.equal(await page.locator('#generate-button').isDisabled(),true);
    assert.equal(await page.locator('#prep-loader').isVisible(),false);
    assert.equal(await page.locator('#generation-status').isVisible(),false);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({status:'passed',browser:'Chromium',checks:['prepare','generate','revision','animated loaders','human-readable server phases','reduced motion','result-only stage timings','legacy history timings','generation failure and retry','stale input','history isolation','font info','dialog keyboard','mobile navigation','request failure recovery'],pageErrors:errors},null,2));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});

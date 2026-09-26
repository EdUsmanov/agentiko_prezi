const $ = selector => document.querySelector(selector);
let packageId = null, resultPackageId = null, busy = false, preview = null, toastTimeout = null;
let slideBudget = null;
let securityLocked = false;
function lockForInjection() {
  if (securityLocked) return;
  securityLocked = true;
  try { sessionStorage.setItem('studio-security-blocked','1'); } catch (_) { /* In-memory lock still applies. */ }
  packageId = null; resultPackageId = null; preview = null;
  stopLoaders(); clearTimeout(toastTimeout); $('#toast').hidden = true;
  document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
  document.querySelectorAll('main,.rail').forEach(node => node.inert = true);
  document.body.classList.add('security-locked');
  setBusy(true);
  $('#security-dialog').showModal(); $('#security-reset').focus();
}
$('#security-dialog').addEventListener('cancel',event => event.preventDefault());
$('#security-dialog').addEventListener('close',() => { if (securityLocked) $('#security-dialog').showModal(); });
$('#security-reset').addEventListener('click',() => {
  $('#prepare-form').reset(); $('#revise-form').reset();
  $('#template').value = ''; $('#images').value = '';
  try { sessionStorage.removeItem('studio-security-blocked'); } catch (_) { /* Storage can be disabled. */ }
  securityLocked = false;
  location.reload();
});
const states = {accepted:'Ожидает запуска', running:'Выполняется', ready:'Подготовлено', completed:'Готово',
  waiting_fonts:'Нужны шрифты', needs_review:'Требуется проверка', failed:'Ошибка', timed_out:'Время истекло', cancelled:'Прервано'};
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}
function toast(text, severity = 'error') {
  clearTimeout(toastTimeout);
  $('#toast').textContent = text; $('#toast').className = severity; $('#toast').hidden = false;
  toastTimeout = setTimeout(() => $('#toast').hidden = true, 15000);
}
async function api(path, options = {}) {
  if (securityLocked && !['GET','HEAD'].includes((options.method || 'GET').toUpperCase())) throw Error('Интерфейс заблокирован проверкой безопасности.');
  const response = await fetch(path, {...options, signal: AbortSignal.timeout(15000)});
  const result = await response.json().catch(() => ({}));
  if (result.detail?.code === 'prompt_injection_detected' || result.security_violation?.code === 'prompt_injection_detected') {
    lockForInjection();
    if (!response.ok) throw Error(result.detail.message);
  }
  if (!response.ok) throw Error(typeof result.detail === 'string' ? result.detail :
    Array.isArray(result.detail) ? result.detail.map(x => `${x.loc?.at(-1) || 'Поле'}: ${x.msg}`).join('; ') :
    `Ошибка сервера (${response.status}). Повторите запрос.`);
  return result;
}
const jsonPost = (path,data) => api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
const fileUrl = (id,name) => `/api/jobs/${encodeURIComponent(id)}/files/${name}`;
$('#images').addEventListener('change',() => {
  const files=[...$('#images').files];
  $('#images-list').replaceChildren(...files.map(file=>el('small','profile-detail',file.name)));
  if (files.length>12 || files.some(f=>f.size>8*1024*1024) || files.reduce((n,f)=>n+f.size,0)>24*1024*1024)
    toast('Лимит: 12 картинок, по 8 МБ, всего 24 МБ.');
});
const wait = ms => new Promise(resolve => setTimeout(resolve,ms));
function setBusy(value) {
  value = value || securityLocked;
  busy = value;
  if (value) { clearTimeout(toastTimeout); $('#toast').hidden = true; }
  for (const form of ['#prepare-form','#revise-form']) $(form).querySelectorAll('input,textarea,select,button').forEach(n => n.disabled = value);
  $('#demo').disabled = value; $('#nav-history').disabled = value;
  $('#generate-button').disabled = value || !packageId || slideBudget?.status === 'needs_input';
  $('#prepare-form').setAttribute('aria-busy',String(value));
}
function dirty() {
  stopAutoWatch();
  if (packageId) api("/api/packages/"+packageId+"/auto-generation/cancel",{method:"POST"}).catch(error => toast("Не удалось отменить автозапуск: "+error.message));
  $("#auto-generation-status").textContent = ""; $("#cancel-auto-generation").hidden = true;
  packageId = null; slideBudget = null; $('#generate-button').disabled = true;
  $('#step-2').classList.remove('active'); $('#step-3').classList.remove('active');
  if (!$('#profile').hidden) {
    $('#prep-state').textContent = 'Нужен новый анализ'; $('#prep-state').className = 'pill neutral'; $('#profile').classList.add('stale');
  }
}
function journalButton(id) {
  const button = el('button','text-button','Открыть журнал');
  button.type = 'button'; button.onclick = () => openDiagnostics(id);
  return button;
}
function displayFontChanges(target, records = []) {
  for (const record of records.filter(item => item.scope === 'font')) {
    target.append(el('p','profile-detail',`Шрифт «${record.template_font}» заменён на «${record.fallback_font}» для поддержки текста. Оформление может отличаться от шаблона.`));
  }
}
const phaseLabels = {
  'Разбор PPTX и дизайн-системы':'Изучаем цвета, шрифты и расположение элементов в вашем шаблоне',
  'Подготовка исходных макетов и фирменной графики':'Сохраняем фирменные фоны, оформление и макеты слайдов',
  'Факты, таблицы и ограничения':'Выделяем главные мысли, числа и таблицы из ваших материалов',
  'Смысловой анализ макетов по тексту и геометрии':'Определяем, для какого содержания подходит каждый макет',
  'Анализ содержания и подготовка плана':'Выстраиваем историю и распределяем материал по слайдам',
  'Проверка композиций и фиксация пакета':'Проверяем, что материал помещается и варианты отличаются',
  'Планирование трёх вариантов':'Готовим три варианта подачи вашего материала',
  'DeepPresenter: выбор и проверка композиций':'Подбираем оформление для каждого слайда — модель работает',
  'Вёрстка, аудит и экспорт':'Размещаем текст и таблицы на слайдах',
  'Проверка покрытия и упаковка':'Проверяем готовые презентации перед выдачей',
};
function humanPhase(label) {
  const prefix = 'Анализ примера организаторов: ';
  if (label.startsWith(prefix)) return 'Изучаем пример оформления: '+label.slice(prefix.length);
  return phaseLabels[label] || label;
}
function setPreparationPhase(label) {
  $('#prep-phase').textContent = humanPhase(label);
}
function setGenerationPhase(label) {
  $('#generation-phase').textContent = humanPhase(label);
}
function stopLoaders() {
  $('#prep-loader').hidden = true; $('#generation-status').hidden = true;
}
function clearGenerationError() {
  $('#generation-error')?.remove();
}
function preparationStopped(error) {
  console.error('Preparation stopped:', error);
  $('#prep-state').textContent = 'Анализ не завершён'; $('#prep-state').className = 'pill neutral';
  toast('Анализ не завершён. Подробности — в журнале задания.','info');
}
function generationError(message) {
  console.error('Generation stopped:', message);
  clearGenerationError();
  const notice = el('div','profile-detail','Генерация не завершена.');
  notice.id = 'generation-error'; notice.setAttribute('role','status');
  if (diagnosticJob) notice.append(journalButton(diagnosticJob));
  $('.generation').append(notice);
}
function beginPreparation(label) {
  stopLoaders(); clearGenerationError();
  $('#results').hidden = true; $('#profile').hidden = true; $('#profile-empty').hidden = true;
  $('#prep-loader').hidden = false; setPreparationPhase(label);
}
function selectedFile() {
  const file = $('#template').files[0];
  dirty();
  if (file && (!/\.(pptx|potx)$/i.test(file.name) || file.size > 60*1024*1024)) {
    $('#template').value = ''; $('#file-label').textContent = 'Перетащите PPTX / POTX или выберите файл';
    return toast('Выберите файл PPTX или POTX размером до 60 МБ.');
  }
  $('#file-label').textContent = file?.name || 'Перетащите PPTX / POTX или выберите файл';
  if (file) $('#reference').value = '';
}
$('#prepare-form').addEventListener('input',dirty);
$('#content').addEventListener('input',() => $('#char-count').textContent = $('#content').value.length.toLocaleString('ru')+' символов');
$('#reference').addEventListener('change',() => {
  dirty(); $('#dropzone').hidden = !!$('#reference').value; $('#template').value = '';
  $('#file-label').textContent = 'Перетащите PPTX / POTX или выберите файл';
});
$('#template').addEventListener('change',selectedFile);
for (const type of ['dragover','dragleave','drop']) $('#dropzone').addEventListener(type,event => {
  event.preventDefault(); if (busy) return;
  $('#dropzone').classList.toggle('dragover',type === 'dragover');
  if (type === 'drop' && event.dataTransfer.files.length) {
    if (event.dataTransfer.files.length !== 1) return toast('Загрузите один шаблон PPTX или POTX.');
    $('#template').files = event.dataTransfer.files; selectedFile();
  }
});
function displayProfile(job) {
  packageId = job.id;
  armAutoGeneration(job);
  slideBudget = job.analysis?.slide_budget || null;
  if (job.constraints?.confirm_plan) slideBudget = {...slideBudget, confirm_plan:true};
  $('#generate-button').textContent = slideBudget?.status === 'adjusted'
    ? `Создать три презентации по ${slideBudget.planned} слайдов ↗` : 'Создать три презентации ↗';
  $('#profile-empty').hidden = true; $('#prep-loader').hidden = true; $('#profile').hidden = false;
  $('#profile').classList.remove('stale'); $('#profile').replaceChildren();
  const t = job.template;
  $('#profile').append(el('div','profile-name',t.name));
  const swatches = el('div','swatches');
  t.colors.slice(0,7).forEach(color => {
    if (!/^#[0-9a-f]{6}$/i.test(color)) return;
    const swatch = el('span','swatch'); swatch.style.backgroundColor = color; swatch.title = color; swatches.append(swatch);
  });
  const stats = el('div','profile-stats');
  [[t.slide_count,'слайдов разобрано'],[t.patterns.length,'композиций'],[t.layout_count,'макетов']].forEach(([value,label]) => {
    const item = el('div'); item.append(el('strong','',value),el('small','',label)); stats.append(item);
  });
  $('#profile').append(swatches,stats,
    el('p','profile-detail',`Шрифт: ${t.font} (${t.font_origin?.kind === 'embedded' ? 'из шаблона' : t.font_origin?.kind === 'glyph_fallback' ? 'автоматическая замена для поддержки текста' : 'точное локальное начертание'})`),
    el('p','profile-detail',`${job.content.facts} фактов · ${job.content.tables} таблиц · ${job.content.images || 0} картинок · ${slideBudget?.status === 'needs_input' ? 'нужно сократить материал' : `${job.analysis?.planned_slides ?? job.constraints.slides} слайдов на вариант`}`));
  if (t.font_roles) {
    const labels = {title:'Заголовки',body:'Основной текст',table:'Таблицы',footer:'Колонтитулы',chart:'Графики'};
    Object.entries(t.font_roles).forEach(([role,id]) => {
      const asset = (t.font_assets || []).find(a => a.id === id);
      if (asset) $('#profile').append(el('p','profile-detail',`${labels[role] || role}: ${asset.requested}`));
    });
    const report = el('a','text-button','Отчёт о шрифтах ↗'); report.href = fileUrl(job.id,'font-model.json'); report.target = '_blank'; report.rel = 'noopener';
    $('#profile').append(report);
  }
  const analysis = job.analysis;
  if (analysis) {
    const rows = [['Технический разбор','выполнен'],
      ['Смысловой анализ шаблона',analysis.template_semantics?.status === 'completed' ? (analysis.template_semantics.method === 'text_geometry_and_source_images' ? 'текст, геометрия и изображения, VL' : 'текст и геометрия, LLM') : analysis.template_semantics?.status === 'partial' ? 'частично: непроверенные макеты исключены' : 'не выполнен'],
      ['Структура документа',analysis.document_structure?.status === 'completed' ? 'заголовки, содержание и указания выделены' : analysis.document_structure?.status === 'degraded' ? 'частично по модели; остальные блоки сохранены без сокращения' : 'детерминированный разбор'],
      ['Архетипы содержания',analysis.archetypes?.status === 'completed' ? `${analysis.archetypes.units.length} смысловых блоков проверено по каталогу` : analysis.archetypes?.status === 'degraded' ? 'частично: неподтверждённые блоки сохранены как обычный текст' : 'не определялись — нужен новый анализ в режиме LLM'],
      ['Разделители',analysis.section_dividers?.reason === 'reserved_before_content_allocation' ? 'зарезервированы в сценарии' : 'статус в отчёте анализа'],
      ['План содержания',analysis.planning_status === 'needs_input' ? 'нужно сократить материал' : analysis.planning_source === 'explicit_author_storyboard' ? 'по вашему сценарию' : analysis.planning_source === 'semantic_summary_storyboard' ? 'главные мысли и данные распределены моделью' : analysis.planning_source === 'model' ? 'подготовлен моделью' : 'экстрактивный'],
      ['Отрисовка макетов',`${analysis.native_render?.patterns || 0} композиций`],
      ['Визуальная проверка слайдов','статус в готовом результате'],
      ['Различия композиций',analysis.composition_preview?.verified ? 'проверены в коде' : 'требуют проверки']];
    const list = el('dl','analysis-checks'); rows.forEach(([label,value]) => list.append(el('dt','',label),el('dd','',value)));
    $('#profile').append(list);
    if (analysis.references?.length) $('#profile').append(el('p','profile-detail',`Использована сохранённая библиотека: ${analysis.references.length} примера. Повторных запросов к модели для них не было. Палитры не смешиваются.`));
    if (job.template?.color_analysis?.status === 'completed') {
      const colorReport = el('a','text-button','Цвета по ролям · JSON ↗'); colorReport.href = fileUrl(job.id,'color-model.json'); colorReport.target = '_blank'; colorReport.rel = 'noopener';
      $('#profile').append(colorReport,el('span','',' · '));
    }
    const report = el('a','text-button','Отчёт анализа JSON ↗'); report.href = fileUrl(job.id,'analysis.json'); report.target = '_blank'; report.rel = 'noopener';
    $('#profile').append(report,el('span','',' · '));
  } else $('#profile').append(el('p','notice warning','Старый пакет без смыслового анализа. Для нового анализа загрузите материалы повторно.'));
  const design = el('a','text-button','DESIGN.md ↗'); design.href = fileUrl(job.id,'DESIGN.md'); design.target = '_blank'; design.rel = 'noopener'; $('#profile').append(design);
  const outline = analysis?.canonical_storyboard || analysis?.storyboard || [];
  if (outline.length) {
    $('#profile').append(el('h3','','Предложенный план · '+outline.length+' слайдов'));
    const list = el('ol','plan-outline');
    for (const [index,slide] of outline.entries()) {
      const row=el('li'); row.append(el('strong','',slide.title));
      const claims=analysis?.editorial?.plan?.slides?.[index]?.bullets;
      if (claims?.length) {
        const bullets=el('ul');
        for (const claim of claims) bullets.append(el('li','profile-detail',claim.text));
        row.append(bullets);
      }
      list.append(row);
    }
    $('#profile').append(list);
  }
  if (analysis?.narrative) $('#profile').append(el('p','profile-detail',analysis.narrative.message));
  if (analysis?.editorial?.omitted?.length) {
    const details=el('details','selection-details');
    details.append(el('summary','',`Исключённые фрагменты: ${analysis.editorial.omitted.length}`));
    for (const item of analysis.editorial.omitted) details.append(el('p','profile-detail',item.source_text+' — '+item.explanation));
    $('#profile').append(details);
  }
  const change = el('form','plan-revision');
  const number = el('input'); number.type='number'; number.min=1; number.max=30;
  number.value=outline.length || job.constraints.slides; number.required=true;
  number.id='plan-slide-count'; number.setAttribute('aria-label','Желаемое количество слайдов');
  const wishes = el('textarea'); wishes.maxLength=5000; wishes.rows=2;
  wishes.placeholder='Что выделить и что сократить'; wishes.setAttribute('aria-label','Пожелания к новому плану');
  const submit = el('button','secondary','Пересобрать план'); submit.type='submit';
  change.append(el('p','profile-detail','Не подходит объём? Укажите число: заново выделим главное из исходного текста и подберём макеты.'),number,wishes,submit);
  let cancellation = null;
  change.addEventListener('input',() => {
    if (!cancellation) cancellation=api(`/api/packages/${job.id}/auto-generation/cancel`,{method:'POST'});
    cancellation.catch(error => toast(error.message));
    $('#auto-generation-status').textContent='Изменение плана: автоматический запуск отменяется.';
  });
  change.onsubmit=async event => {
    event.preventDefault(); if (busy || packageId!==job.id) return;
    const slides=Number(number.value); const instructions=wishes.value.trim() || 'Выделить главное, сократить текст и пересобрать план под выбранное количество слайдов.';
    stopAutoWatch(); setBusy(true); submit.disabled=true; beginPreparation('Заново выделяем главное из исходного текста');
    try { if (cancellation) await cancellation; await finishPreparation(await jsonPost(`/api/packages/${job.id}/revise`,{slides,instructions})); }
    catch (error) { preparationStopped(error); }
    finally { stopLoaders(); setBusy(false); submit.disabled=false; }
  };
  $('#profile').append(change);
  displayFontChanges($('#profile'),analysis?.font_substitutions || t.font_substitutions);
  $('#profile').append(journalButton(job.id));
  $('#prep-state').textContent = slideBudget?.status === 'needs_input' ? 'Нужно уточнить план' : 'Подготовлено';
  $('#prep-state').className = 'pill '+(slideBudget?.status === 'needs_input' ? 'neutral' : 'good');
  $('#step-2').classList.add('active'); $('#generate-button').disabled = busy || slideBudget?.status === 'needs_input';
}
async function watchJob(id,onProgress) {
  diagnosticJob = id;
  let failures = 0;
  for (;;) {
    try {
      const job = await api(`/api/jobs/${id}`); failures = 0; onProgress(job);
      if (!['accepted','running'].includes(job.state)) return job;
    } catch (error) {
      if (++failures >= 3) throw Error('Связь с сервером потеряна. Задание может продолжать выполняться. Проверьте историю перед повторным запуском.');
    }
    await wait(1000);
  }
}
async function finishPreparation(job,autoGenerate = false) {
  const done = await watchJob(job.id,state => setPreparationPhase(state.phase || 'Подготовка'));
  if (done.state === 'waiting_fonts') { displayMissingFonts(done); return; }
  if (done.state !== 'ready') throw Error(done.error || 'Анализ остановлен');
  displayProfile(done);
}
function displayMissingFonts(job) {
  packageId = null; slideBudget = null; diagnosticJob = job.id;
  $('#profile-empty').hidden = true; $('#prep-loader').hidden = true; $('#profile').hidden = false;
  $('#profile').classList.remove('stale'); $('#profile').replaceChildren();
  $('#profile').append(el('h3','','Добавьте шрифты'),el('p','profile-detail','Добавьте TTF и повторите проверку.'));
  for (const font of job.missing_fonts || []) $('#profile').append(el('p','profile-detail',font.requested));
  const report = el('a','text-button','Отчёт о шрифтах ↗'); report.href = fileUrl(job.id,'font-model.json'); report.target = '_blank'; report.rel = 'noopener';
  const retry = el('button','secondary','Проверить шрифты повторно');
  retry.onclick = async () => {
    if (busy) return;
    setBusy(true); retry.disabled = true;
    try { await finishPreparation(await api(`/api/packages/${job.id}/retry-fonts`,{method:'POST'})); }
    catch (error) { preparationStopped(error); }
    finally { setBusy(false); retry.disabled = false; }
  };
  $('#profile').append(report,retry,journalButton(job.id));
  $('#prep-state').textContent = 'Нужны шрифты'; $('#prep-state').className = 'pill caution';
  $('#generate-button').disabled = true;
}
$('#prepare-form').addEventListener('submit',async event => {
  event.preventDefault(); if (busy) return;
  if (!$('#reference').value && !$('#template').files.length) return toast('Выберите шаблон PPTX / POTX или пример организаторов.');
  if (!$('#content').value.trim()) return toast('Добавьте текст презентации.');
  // Disabled controls are omitted by FormData: collect before locking the UI.
  const data = new FormData(event.target);
  if (!$('#images').files.length) data.delete('images');
  if ($('#reference').value) data.delete('template'); else data.delete('reference_id');
  if (!data.get('slides')) data.delete('slides');
  dirty(); setBusy(true); beginPreparation('Загрузка материалов');
  $('#prep-state').textContent = 'Анализируем'; $('#prep-state').className = 'pill neutral';
  try { await finishPreparation(await api('/api/prepare',{method:'POST',body:data})); }
  catch (error) {
    preparationStopped(error);
  } finally { stopLoaders(); setBusy(false); }
});
async function startGeneration(nested = false) {
  if (!packageId || (busy && !nested)) return;
  stopAutoWatch(); $("#cancel-auto-generation").hidden = true;
  setBusy(true); $('#generation-status').hidden = false; $('#results').hidden = true;
  clearGenerationError();
  setGenerationPhase('Передаём подготовленные материалы на генерацию');
  try {
    const job = await jsonPost('/api/generate',{package_id:packageId,
      accept_adjusted_slide_count:slideBudget?.status === 'adjusted' || !!slideBudget?.confirm_plan});
    const done = await watchJob(job.id,state => {
      setGenerationPhase((state.slide_count_decision?.mode==='automatic' ? state.slide_count_decision.message+' ('+state.slide_count_decision.count+'). ' : '')+(state.phase || 'Готовим запуск генерации'));
    });
    if (!['completed','needs_review'].includes(done.state)) throw Error(done.error || 'Генерация прервана');
    showResults(done);
  } catch (error) { generationError(error.message); }
  finally { stopLoaders(); if (!nested) setBusy(false); }
}
$('#generate-button').addEventListener('click',() => startGeneration());
const duration = seconds => typeof seconds === 'number' && Number.isFinite(seconds) && seconds >= 0
  ? seconds.toLocaleString('ru-RU',{minimumFractionDigits:1,maximumFractionDigits:1})+' сек.' : 'Нет данных';
async function showTimings(job) {
  const target = $('#result-timings');
  target.dataset.jobId = job.id;
  const analysis = el('dd','',duration(job.analysis_seconds));
  const preparation = el('div'); preparation.append(el('dt','','Анализ материалов'),analysis);
  const generation = el('div'); generation.append(el('dt','','Генерация трёх презентаций'),el('dd','',duration(job.elapsed_seconds)));
  target.replaceChildren(preparation,generation);
  // Older results did not copy preparation timing; resolve their own package, not the editor's.
  if (job.analysis_seconds == null && job.package_id) {
    try {
      const source = await api(`/api/jobs/${encodeURIComponent(job.package_id)}`);
      if (target.dataset.jobId === job.id) analysis.textContent = duration(source.analysis_seconds);
    } catch { /* Missing historical preparation must not block downloads. */ }
  }
}
function showResults(job) {
  // History results and the current editor have separate package identities.
  resultPackageId = job.package_id; diagnosticJob = job.id;
  clearTimeout(toastTimeout); $('#toast').hidden = true;
  $('#results').hidden = false; $('#step-3').classList.add('active');
  stopLoaders(); clearGenerationError(); showTimings(job);
  $('#result-summary').textContent = `${states[job.state] || job.state} · ${job.variants.reduce((sum,v) => sum+v.slides,0)} слайдов${job.model_mode === 'extractive' ? ' · без LLM' : ''}`;
  if (job.slide_count_decision?.mode==='automatic') $('#result-summary').textContent += ' · '+job.slide_count_decision.message+': '+job.slide_count_decision.count;
  $('#result-summary').className = job.state === 'needs_review' ? 'status-warning' : '';
  if (job.engine?.engine === 'deeppresenter') $('#result-summary').textContent += ' · DeepPresenter Design';
  if (job.engine?.engine === 'pptagent_v02') $('#result-summary').textContent += ' · PPTAgent v0.2.0';
  $('#download-all').href = fileUrl(job.id,'presentations.zip'); $('#manifest-link').href = fileUrl(job.id,'manifest.json');
  $('#result-grid').replaceChildren(); $('#audit-list').replaceChildren();
  for (const variant of job.variants) {
    const card = el('article','result-card'), button = el('button','preview-button');
    button.type = 'button'; button.setAttribute('aria-label',`Открыть ${variant.title}`);
    const img = el('img','result-thumb'); img.src = fileUrl(job.id,`${variant.key}/slide-1.png`); img.alt = variant.title; img.loading = 'lazy';
    button.append(img); button.onclick = () => {
      preview = {id:job.id,key:variant.key,count:variant.slides,index:1};
      $('#preview-title').textContent = variant.title; updatePreview(); $('#preview-dialog').showModal();
    };
    card.append(button,el('h3','',variant.title),el('div','result-meta',`${variant.slides} слайдов · редактируемый PPTX`));
    const links = el('div','downloads');
    for (const format of ['pptx','pdf','html']) {
      const link = el('a','',format.toUpperCase()+' ↗'); link.href = fileUrl(job.id,`${variant.key}/deck.${format}`); link.target = '_blank'; link.rel = 'noopener'; links.append(link);
    }
    card.append(links); $('#result-grid').append(card);
  }
  $('#audit-list').append(el('p','profile-detail','Результаты проверок сохранены в журнале задания и отчёте JSON.'),journalButton(job.id));
  displayFontChanges($('#audit-list'),job.font_substitutions);
  $('#results').scrollIntoView({behavior:'smooth',block:'start'});
}
function updatePreview() {
  $('#preview-image').src = fileUrl(preview.id,`${preview.key}/slide-${preview.index}.png`);
  $('#preview-image').alt = `Слайд ${preview.index} из ${preview.count}`;
  $('#slide-counter').textContent = `${preview.index} / ${preview.count}`;
  $('#prev-slide').disabled = preview.index === 1; $('#next-slide').disabled = preview.index === preview.count;
}
$('#prev-slide').onclick = () => { if (preview.index > 1) { preview.index--; updatePreview(); } };
$('#next-slide').onclick = () => { if (preview.index < preview.count) { preview.index++; updatePreview(); } };
$('#close-preview').onclick = () => $('#preview-dialog').close();
$('#preview-dialog').addEventListener('keydown',event => {
  if (event.key === 'ArrowLeft') { event.preventDefault(); $('#prev-slide').click(); }
  if (event.key === 'ArrowRight') { event.preventDefault(); $('#next-slide').click(); }
});
$('#revise-form').addEventListener('submit',async event => {
  event.preventDefault(); if (busy || !resultPackageId) return;
  const instructions = $('#revision').value.trim(); if (!instructions) return toast('Добавьте указание для новой версии.');
  setBusy(true); beginPreparation('Подготовка новой версии');
  try {
    const job = await jsonPost(`/api/packages/${resultPackageId}/revise`,{instructions});
    packageId = null;
    $('#prep-state').textContent = 'Новая версия'; $('#prep-state').className = 'pill neutral';
    $('#workspace').scrollIntoView({behavior:'smooth'}); await finishPreparation(job,true);
  } catch (error) { preparationStopped(error); }
  finally { stopLoaders(); setBusy(false); }
});
async function resumeJob(job) {
  if (busy) return; setBusy(true);
  try {
    if (job.kind === 'preparation') { beginPreparation(job.phase || 'Анализируем материалы'); await finishPreparation(job); }
    else {
      clearGenerationError(); $('#results').hidden = true; $('#generation-status').hidden = false;
      const done = await watchJob(job.id,state => {
        setGenerationPhase((state.slide_count_decision?.mode==='automatic' ? state.slide_count_decision.message+' ('+state.slide_count_decision.count+'). ' : '')+(state.phase || 'Получаем состояние генерации'));
      });
      if (['completed','needs_review'].includes(done.state)) showResults(done); else generationError(done.error || states[done.state]);
    }
  } catch (error) { if (job.kind === 'generation') generationError(error.message); else preparationStopped(error); }
  finally { stopLoaders(); setBusy(false); }
}
$('#nav-history').onclick = async () => {
  if (busy) return;
  $('#history').hidden = false; $('#history-list').replaceChildren();
  try {
    const jobs = await api('/api/jobs');
    if (!jobs.length) $('#history-list').append(el('p','profile-detail','Пока нет запусков. Начните с загрузки материалов.'));
    for (const job of jobs) {
      const row = el('div','history-item'), info = el('div');
      info.append(el('div','',job.kind === 'preparation' ? job.template_name || 'Подготовка шаблона' : 'Три презентации'),el('small','',`${new Date(job.created*1000).toLocaleString('ru')} · ${states[job.state] || job.state}`)); row.append(info);
      let button;
      if (['completed','needs_review'].includes(job.state)) {
        button = el('button','secondary','Открыть'); button.onclick = () => { if (!busy) showResults(job); };
      } else if (job.state === 'waiting_fonts') {
        button = el('button','secondary','Проверить шрифты'); button.onclick = () => { if (!busy) displayMissingFonts(job); };
      } else if (job.state === 'ready') {
        button = el('button','secondary','Использовать пакет'); button.onclick = () => {
          if (busy) return; displayProfile(job);
          toast('Выбран сохранённый пакет. Поля ввода слева не относятся к нему; их изменение потребует нового анализа.','info');
          $('#workspace').scrollIntoView({behavior:'smooth'});
        };
      } else if (['accepted','running'].includes(job.state)) {
        button = el('button','secondary','Продолжить наблюдение'); button.onclick = () => resumeJob(job);
      }
      if (button) row.append(button);
      const logs = el("button","text-button","Журнал"); logs.onclick = () => openDiagnostics(job.id); row.append(logs);
      if (job.error) info.append(el('small','status-warning',job.error));
      $('#history-list').append(row);
    }
    $('#history').scrollIntoView({behavior:'smooth'});
  } catch (error) { toast(error.message); }
};
$('#close-history').onclick = () => $('#history').hidden = true;
$('#nav-create').onclick = () => $('#workspace').scrollIntoView({behavior:'smooth'});
$('#demo').onclick = async () => {
  if (busy) return;
  try {
    const response = await fetch('/static/demo.md'); if (!response.ok) throw Error('Демо недоступно');
    $('#content').value = await response.text(); $('#content').dispatchEvent(new Event('input'));
    $('#audience').value = 'Руководители компании'; $('#slides').value = 'standard';
    $('#instructions').value = 'Сохранить все исходные факты. Не придумывать показатели.';
    dirty(); toast('Загружены демонстрационные вымышленные материалы.','info');
  } catch (error) { toast(error.message); }
};
async function updateRuntime() {
  try {
    const runtime = await api('/api/runtime');
    $('#runtime-status').textContent = runtime.restart_required
      ? 'Пайплайн изменился: требуется перезапуск приложения.' : '';
  } catch (_) { $('#runtime-status').textContent = 'Статус сервера временно недоступен.'; }
}

let autoWatch = null, diagnosticJob = null, logWatch = null;
function stopAutoWatch() { clearTimeout(autoWatch); autoWatch = null; }
function armAutoGeneration(job) {
  stopAutoWatch(); diagnosticJob = job.id;
  const pid = job.id;
  async function tick() {
    if (packageId !== pid || securityLocked) return;
    try {
      const current = await api('/api/jobs/'+pid);
      if (packageId !== pid) return;
      const status = $('#auto-generation-status');
      $('#cancel-auto-generation').hidden = current.auto_generation !== 'scheduled';
      if (current.generation_id) {
        status.textContent = 'Генерация запущена.';
        if (!busy) await resumeJob(await api('/api/jobs/'+current.generation_id));
        else autoWatch = setTimeout(tick,1000);
        return;
      }
      if (current.auto_generation === 'scheduled') {
        const seconds = Math.max(0,Math.ceil(current.auto_generate_at-Date.now()/1000));
        status.textContent = seconds ? 'Через '+seconds+' сек. система примет предложенные '+(current.analysis?.planned_slides || current.constraints.slides)+' слайдов и запустит три презентации. Можно изменить план или отменить.' : 'Сервер запускает генерацию…';
      } else if (current.auto_generation === 'needs_confirmation') {
        status.textContent = 'Проверьте структуру и количество слайдов. Подтвердите план кнопкой генерации или измените его.';
        return;
      } else if (['cancelled','blocked'].includes(current.auto_generation)) {
        status.textContent = current.auto_generation === 'cancelled' ? 'Автозапуск отменён. Можно запустить вручную.' : 'Автозапуск не выполнен. Подробности — в журнале задания.';
        return;
      } else if (!current.auto_generation) {
        status.textContent = 'Сохранённый пакет. Для генерации нажмите кнопку запуска.';
        return;
      }
    } catch (_) { $('#auto-generation-status').textContent = 'Нет связи. Серверный автозапуск не отменён; проверяем состояние…'; }
    autoWatch = setTimeout(tick,1000);
  }
  autoWatch = setTimeout(tick,250);
}
$('#cancel-auto-generation').onclick = async () => {
  if (!packageId) return;
  try { await api('/api/packages/'+packageId+'/auto-generation/cancel',{method:'POST'}); }
  catch (error) { toast(error.message); }
};
async function openDiagnostics(id) {
  clearTimeout(logWatch);
  const dialog = $('#diagnostics-dialog');
  if (!dialog.open) dialog.showModal();
  $('#diagnostics-title').textContent = 'Журнал задания '+id.slice(0,8);
  const path = '/api/jobs/'+id+'/diagnostics';
  $('#download-diagnostics').href = path+'?download=true';
  let cursor = -1, events = [];
  async function refresh() {
    if (!dialog.open || dialog.dataset.target !== id) return;
    try {
      const report = await api(path+'?after='+cursor);
      if (!dialog.open || dialog.dataset.target !== id) return;
      events = [...events,...report.events].slice(-500);
      if (events.length) cursor = events.at(-1).id;
      report.events = events;
      report.display = 'Последние 500 событий. Полный сохранённый журнал — «Скачать JSON».';
      $('#diagnostics-output').textContent = JSON.stringify(report,null,2);
    } catch (error) { $('#diagnostics-output').textContent = error.message; }
    logWatch = setTimeout(refresh,2000);
  }
  dialog.dataset.target = id;
  await refresh();
}
$('#open-diagnostics').onclick = () => diagnosticJob ? openDiagnostics(diagnosticJob) : toast('Сначала выберите запуск в истории.','info');
$('#close-diagnostics').onclick = () => $('#diagnostics-dialog').close();
$('#diagnostics-dialog').addEventListener('close',() => clearTimeout(logWatch));

async function init() {
  try {
    const [health,references] = await Promise.all([api('/api/health'),api('/api/references')]);
    $('#model-mode').textContent = health.model_mode === 'extractive' ? 'Автономно · без LLM' : `Модель: ${health.model_id}`;
    if (health.engine === 'deeppresenter') {
      $('#model-mode').textContent += ' · DeepPresenter';
      if (!health.deeppresenter?.ready) toast('DeepPresenter не готов: установите дополнительные зависимости на сервере.');
    }
    if (health.engine === 'pptagent_v02') {
      $('#model-mode').textContent += ' · PPTAgent v0.2.0';
      if (!health.pptagent?.ready) toast(health.pptagent?.reason || 'PPTAgent не готов: проверьте Docker.');
    }
    $('#model-disclosure').textContent = health.model_mode === 'api'
      ? 'При анализе модель получает текст и параметры макетов. При включённой визуальной проверке отрисованные изображения готовых слайдов также отправляются настроенному провайдеру модели. Исходный файл PPTX / POTX не отправляется. Внешние ссылки не открываются.'
      : 'Автономный режим: материалы обрабатываются локально, без смыслового анализа LLM. Внешние ссылки не открываются.';
    if (health.model_mode==='api' && !health.features?.vlm) $('#model-disclosure').textContent += ' Проверка изображений сейчас отключена; её включение требует разрешения на передачу PNG.';
    if (health.features?.vlm) $('#model-disclosure').textContent += ' Загруженные вами картинки также будут видны провайдеру в составе слайдов, но не передаются планировщику.';
    if (health.features?.download_open_fonts) $('#model-disclosure').textContent += ' Недостающие открытые шрифты ищем в Google Fonts: туда передаётся только название шрифта.';
    for (const reference of references) { const option = el('option','',reference.name); option.value = reference.id; $('#reference').append(option); }
    const jobs = await api("/api/jobs");
    const active = jobs.find(job => ["accepted","running"].includes(job.state));
    const pending = jobs.find(job => job.state === "ready" && job.auto_generation === "scheduled");
    if (!busy && !packageId && !securityLocked) {
      if (active) resumeJob(active);
      else if (pending) displayProfile(pending);
    }
    // Never silently substitute an organizer example for a user upload.
  } catch (error) { $('#model-mode').textContent = 'Нет связи с сервером'; toast(error.message); }
}
init();
try { if (sessionStorage.getItem('studio-security-blocked') === '1') lockForInjection(); } catch (_) { /* No persistent storage. */ }
updateRuntime();
setInterval(() => { if (!document.hidden) updateRuntime(); }, 10000);

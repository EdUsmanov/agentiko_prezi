const $ = selector => document.querySelector(selector);
let packageId = null, resultPackageId = null, busy = false, preview = null, toastTimeout = null;
let activeHistoryId = null;
let sourcePackageId = null;
let sourceTemplateName = '';
let slideBudget = null;
let autoGenerationPending = false;
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
const uploadPrompt = 'Перетащите PPTX или POTX сюда либо выберите файл';
function wordForm(value, forms) {
  const n = Math.abs(Number(value)), lastTwo = n % 100, last = n % 10;
  return lastTwo >= 11 && lastTwo <= 14 ? forms[2] : last === 1 ? forms[0] : last >= 2 && last <= 4 ? forms[1] : forms[2];
}
const counted = (value, forms) => `${value} ${wordForm(value, forms)}`;
const slidesText = value => counted(value,['слайд','слайда','слайдов']);
$('#images').addEventListener('change',() => {
  const files=[...$('#images').files];
  $('#images-list').replaceChildren(...files.map(file=>el('small','profile-detail',file.name)));
  if (files.length>12 || files.some(f=>f.size>8*1024*1024) || files.reduce((n,f)=>n+f.size,0)>24*1024*1024)
    toast('Можно добавить до 12 изображений: по 8 МБ на файл, всего 24 МБ.');
});
const wait = ms => new Promise(resolve => setTimeout(resolve,ms));
function setBusy(value) {
  value = value || securityLocked;
  busy = value;
  if (value) { clearTimeout(toastTimeout); $('#toast').hidden = true; }
  for (const form of ['#prepare-form','#revise-form']) $(form).querySelectorAll('input,textarea,select,button').forEach(n => n.disabled = value);
  document.querySelectorAll('input[name="variant-count"]').forEach(input => input.disabled = value);
  $('#demo').disabled = value;
  $('#generate-button').disabled = value || !packageId || slideBudget?.status === 'needs_input';
  $('#prepare-form').setAttribute('aria-busy',String(value));
}
function dirty() {
  stopAutoWatch();
  activeHistoryId = null;
  if (packageId && autoGenerationPending) api("/api/packages/"+packageId+"/auto-generation/cancel",{method:"POST"}).catch(error => toast("Не удалось отменить автозапуск: "+error.message));
  autoGenerationPending = false;
  $("#auto-generation-status").textContent = ""; $("#cancel-auto-generation").hidden = true;
  packageId = null; slideBudget = null; $('#generate-button').disabled = true;
  document.body.classList.remove('analysis-ready','results-ready');
  $('#intro-title').textContent = 'Новая презентация';
  $('#intro-description').textContent = sourcePackageId
    ? `Дизайн-система «${sourceTemplateName}» уже изучена. Добавьте новый текст для презентации.`
    : 'Загрузите шаблон и добавьте текст. После анализа выберите одну или три презентации.';
  $('#edit-materials').hidden = true; $('#generation-panel').hidden = true; $('#analysis-details').hidden = true;
  $('#analysis-template').hidden = true; $('#analysis-variants').hidden = true;
  $('#variant-guide').hidden = false; $('#results').hidden = true;
  $('#step-2').classList.remove('active'); $('#step-3').classList.remove('active');
  if (!$('#profile').hidden) {
    $('#prep-state').textContent = 'Нужен новый анализ'; $('#prep-state').className = 'pill neutral';
    $('#profile').hidden = true; $('#profile-empty').hidden = !!sourcePackageId;
    $('#reuse-profile').hidden = !sourcePackageId;
    $('#profile-empty h3').textContent = 'Материалы изменены';
    $('#profile-empty p').textContent = 'Запустите анализ, чтобы обновить план презентации.';
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
  'Планирование одного варианта':'Готовим план вашей презентации',
  'DeepPresenter: выбор и проверка композиций':'Подбираем оформление слайдов',
  'Вёрстка, аудит и экспорт':'Размещаем текст и таблицы на слайдах',
  'Проверка покрытия и упаковка':'Проверяем готовые презентации перед выдачей',
};
function humanPhase(label) {
  // Raw retry/provider diagnostics stay in the job journal.
  if (/повторяем незавершённый|проблемн|ошибк|error|exception|traceback|inputrejected|не удалось/i.test(label))
    return 'Уточняем результат проверки';
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
  $('#prep-state').textContent = sourcePackageId ? 'Изучена' : 'Анализ не завершён';
  $('#prep-state').className = 'pill '+(sourcePackageId ? 'good' : 'neutral');
  toast('Анализ не завершён. Подробности — в журнале задания.','info');
  loadHistory();
}
function generationError(message) {
  console.error('Generation stopped:', message);
  clearGenerationError();
  const notice = el('div','profile-detail','Генерация не завершена.');
  notice.id = 'generation-error'; notice.setAttribute('role','status');
  if (diagnosticJob) notice.append(journalButton(diagnosticJob));
  $('.generation').append(notice);
  loadHistory();
}
function beginPreparation(label) {
  stopLoaders(); clearGenerationError();
  activeHistoryId = null;
  document.body.classList.remove('analysis-ready','results-ready');
  syncNavState();
  $('#auto-generation-status').textContent = ''; $('#cancel-auto-generation').hidden = true;
  $('#results').hidden = true; $('#profile').hidden = true; $('#profile-empty').hidden = true;
  $('#variant-guide').hidden = true; $('#generation-panel').hidden = true; $('#analysis-details').hidden = true;
  $('#analysis-template').hidden = true; $('#analysis-variants').hidden = true;
  $('#edit-materials').hidden = true;
  $('#prep-loader .loader-copy strong').textContent = sourcePackageId ? 'Готовим новый материал' : 'Разбираем визуальный язык';
  $('#prep-loader').hidden = false; setPreparationPhase(label);
}
function clearReuseSource() {
  sourcePackageId = null;
  sourceTemplateName = '';
  document.body.classList.remove('reuse-ready');
  $('#template-field').hidden = false; $('#template').required = true;
  $('#reuse-source').hidden = true; $('#reuse-profile').hidden = true;
  $('#prepare-button').replaceChildren('Проанализировать материалы ',el('span','','→'));
  $('#prep-state').textContent = 'Ожидание материалов'; $('#prep-state').className = 'pill neutral';
  $('#profile-empty').hidden = false;
}
$('#change-template').onclick = () => {
  clearReuseSource();
  $('#intro-description').textContent = 'Загрузите шаблон и добавьте текст. После анализа выберите одну или три презентации.';
  $('#template').click();
};
function selectedFile() {
  const file = $('#template').files[0];
  if (sourcePackageId) clearReuseSource();
  dirty();
  if (file && (!/\.(pptx|potx)$/i.test(file.name) || file.size > 60*1024*1024)) {
    $('#template').value = ''; $('#file-label').textContent = uploadPrompt;
    return toast('Выберите файл PPTX или POTX размером до 60 МБ.');
  }
  $('#file-label').textContent = file?.name || uploadPrompt;
}
$('#prepare-form').addEventListener('input',dirty);
$('#content').addEventListener('input',() => $('#char-count').textContent = $('#content').value.length.toLocaleString('ru')+' символов');
$('#template').addEventListener('change',selectedFile);
for (const type of ['dragover','dragleave','drop']) $('#dropzone').addEventListener(type,event => {
  event.preventDefault(); if (busy) return;
  $('#dropzone').classList.toggle('dragover',type === 'dragover');
  if (type === 'drop' && event.dataTransfer.files.length) {
    if (event.dataTransfer.files.length !== 1) return toast('Загрузите один шаблон PPTX или POTX.');
    $('#template').files = event.dataTransfer.files; selectedFile();
  }
});
const variantCount = () => Number(document.querySelector('input[name="variant-count"]:checked')?.value || 3);
function setVariantCount(count) {
  const input = document.querySelector(`input[name="variant-count"][value="${count === 1 ? 1 : 3}"]`);
  input.checked = true;
  updateGenerationChoice();
}
function updateGenerationChoice() {
  const one = variantCount() === 1;
  $('#generation-loader-title').textContent = one ? 'Собираем презентацию' : 'Собираем три презентации';
  if (!$('#variant-choice').hidden) $('#generate-button').replaceChildren(
    one ? 'Создать презентацию ' : 'Создать три презентации ', el('span','','↗'));
  const variants = $('#analysis-variants');
  const title = variants.querySelector('h2'), lead = variants.querySelector('.analysis-variants-lead');
  if (title) title.textContent = one ? 'Одна презентация' : 'Три варианта презентации';
  if (lead) lead.textContent = one
    ? 'Подготовим одну презентацию с акцентом на главных выводах.'
    : 'Создадим три версии с разными акцентами, чтобы вы выбрали подходящую.';
  variants.querySelectorAll('.analysis-variant-card').forEach((card,index) => card.hidden = one && index > 0);
}
document.querySelectorAll('input[name="variant-count"]').forEach(input => input.addEventListener('change',updateGenerationChoice));
function displayProfile(job) {
  configureReuseSource(job);
  setVariantCount(job.variant_count);
  document.body.classList.add('analysis-ready'); document.body.classList.remove('results-ready');
  $('#reuse-profile').hidden = true;
  syncNavState();
  $('#results').hidden = true;
  $('#intro-title').textContent = job.source_package_id ? 'План готов' : 'Шаблон изучен';
  $('#intro-description').textContent = job.source_package_id
    ? `Новый материал размещён в дизайн-системе «${job.template.name}». Проверьте план и выберите количество презентаций.`
    : `Файл ${job.template.name} проанализирован. Проверьте план и выберите количество презентаций.`;
  packageId = job.id;
  activeHistoryId = job.id;
  if (job.generation_id) {
    stopAutoWatch(); autoGenerationPending=false; diagnosticJob=job.id;
    $('#cancel-auto-generation').hidden=true;
    $('#auto-generation-status').textContent='Готовые презентации доступны. Для новой версии измените план.';
  } else armAutoGeneration(job);
  slideBudget = job.analysis?.slide_budget || null;
  if (job.constraints?.confirm_plan) slideBudget = {...slideBudget, confirm_plan:true};
  $('#variant-choice').hidden = !!job.generation_id;
  updateGenerationChoice();
  if (job.generation_id) $('#generate-button').replaceChildren('Показать готовые презентации ',el('span','','↗'));
  $('#profile-empty').hidden = true; $('#variant-guide').hidden = true; $('#prep-loader').hidden = true; $('#profile').hidden = false;
  $('#analysis-template').hidden = false; $('#analysis-variants').hidden = false;
  $('#generation-panel').hidden = false; $('#analysis-details').hidden = false; $('#edit-materials').hidden = false;
  $('#profile').classList.remove('stale'); $('#profile').replaceChildren(); $('#analysis-detail-content').replaceChildren();
  const detailsTarget = $('#analysis-detail-content');
  const t = job.template;
  const templateCard = $('#analysis-template'); templateCard.replaceChildren();
  const file = el('div','analysis-file');
  const fileIcon = el('span','analysis-file-icon','P'); fileIcon.setAttribute('aria-hidden','true');
  const fileCopy = el('div','analysis-file-copy');
  fileCopy.append(
    el('strong','profile-name',t.name),
    el('p','analysis-file-meta',`${slidesText(t.slide_count)} · ${counted(t.patterns.length,['композиция','композиции','композиций'])} · ${counted(t.layout_count,['макет','макета','макетов'])}`),
  );
  file.append(fileIcon,fileCopy);
  const palette = el('div','analysis-palette'); palette.append(el('span','analysis-label','Основные цвета'));
  const colors = el('div','swatches');
  (t.colors || []).slice(0,7).forEach(color => {
    if (!/^#[0-9a-f]{6}$/i.test(color)) return;
    const swatch = el('span','swatch'); swatch.style.backgroundColor = color; swatch.title = color;
    swatch.setAttribute('aria-label',`Цвет ${color}`); colors.append(swatch);
  });
  const editMaterials = el('button','text-button analysis-edit','Изменить материалы');
  editMaterials.type = 'button'; editMaterials.onclick = () => $('#edit-materials').click();
  palette.append(colors,editMaterials); templateCard.append(file,palette);
  const variants = $('#analysis-variants'); variants.replaceChildren();
  variants.append(el('h2','','Три варианта презентации'),el('p','analysis-variants-lead','Создадим три версии с разными акцентами, чтобы вы выбрали подходящую.'));
  [
    ['Деловая и лаконичная','Главное и решения','Чёткая структура с акцентом на фактах и выводах.'],
    ['Визуальная и наглядная','Данные и доказательства','Больше внимания данным и визуальным акцентам.'],
    ['Креативная и современная','Контекст и развитие','История с акцентом на контексте и следующих шагах.'],
  ].forEach(([title,subtitle,description],index) => {
    const card = el('div','analysis-variant-card');
    const row = el('div','analysis-variant-row'); row.append(el('span','analysis-variant-number',index+1));
    const copy = el('div'); copy.append(el('strong','',title),el('small','',subtitle)); row.append(copy);
    card.append(row,el('p','',description));
    card.append(el('div','analysis-variant-meta',`${slidesText(job.analysis?.planned_slides ?? job.constraints.slides)} · на основе выбранного шаблона`));
    variants.append(card);
  });
  updateGenerationChoice();
  detailsTarget.append(
    el('p','profile-detail',`Шрифт: ${t.font} (${t.font_origin?.kind === 'embedded' ? 'из шаблона' : t.font_origin?.kind === 'glyph_fallback' ? 'автоматическая замена для поддержки текста' : 'точное локальное начертание'})`),
    el('p','profile-detail',`${counted(job.content.facts,['факт','факта','фактов'])} · ${counted(job.content.tables,['таблица','таблицы','таблиц'])} · ${counted(job.content.images || 0,['изображение','изображения','изображений'])} · ${slideBudget?.status === 'needs_input' ? 'нужно сократить материал' : `${slidesText(job.analysis?.planned_slides ?? job.constraints.slides)} на вариант`}`));
  if (t.font_roles) {
    const labels = {title:'Заголовки',body:'Основной текст',table:'Таблицы',footer:'Колонтитулы',chart:'Графики'};
    Object.entries(t.font_roles).forEach(([role,id]) => {
      const asset = (t.font_assets || []).find(a => a.id === id);
      if (asset) detailsTarget.append(el('p','profile-detail',`${labels[role] || role}: ${asset.requested}`));
    });
    const report = el('a','text-button','Отчёт о шрифтах ↗'); report.href = fileUrl(job.id,'font-model.json'); report.target = '_blank'; report.rel = 'noopener';
    detailsTarget.append(report);
  }
  const analysis = job.analysis;
  if (analysis) {
    const rows = [['Технический разбор','выполнен'],
      ['Смысловой анализ шаблона',analysis.template_semantics?.status === 'completed' ? (analysis.template_semantics.method === 'text_geometry_and_source_images' ? 'текст, геометрия и изображения, VL' : 'текст и геометрия, LLM') : analysis.template_semantics?.status === 'partial' ? 'частично: непроверенные макеты исключены' : 'не выполнен'],
      ['Структура документа',analysis.document_structure?.status === 'completed' ? 'заголовки, содержание и указания выделены' : analysis.document_structure?.status === 'degraded' ? 'частично по модели; остальные блоки сохранены без сокращения' : 'детерминированный разбор'],
      ['Архетипы содержания',analysis.archetypes?.status === 'completed' ? `${analysis.archetypes.units.length} смысловых блоков проверено по каталогу` : analysis.archetypes?.status === 'degraded' ? 'частично: неподтверждённые блоки сохранены как обычный текст' : 'не определялись — нужен новый анализ в режиме LLM'],
      ['Разделители',analysis.section_dividers?.reason === 'reserved_before_content_allocation' ? 'зарезервированы в сценарии' : 'статус в отчёте анализа'],
      ['План содержания',analysis.planning_status === 'needs_input' ? 'нужно сократить материал' : analysis.planning_source === 'explicit_author_storyboard' ? 'по вашему сценарию' : analysis.planning_source === 'semantic_summary_storyboard' ? 'главные мысли и данные распределены моделью' : analysis.planning_source === 'model' ? 'подготовлен моделью' : 'экстрактивный'],
      ['Отрисовка макетов',counted(analysis.native_render?.patterns || 0,['композиция','композиции','композиций'])],
      ['Визуальная проверка слайдов','статус в готовом результате'],
      ['Различия композиций',analysis.composition_preview?.verified ? 'проверены в коде' : 'требуют проверки']];
    const list = el('dl','analysis-checks'); rows.forEach(([label,value]) => list.append(el('dt','',label),el('dd','',value)));
    detailsTarget.append(list);
    if (job.template?.color_analysis?.status === 'completed') {
      const colorReport = el('a','text-button','Цвета по ролям · JSON ↗'); colorReport.href = fileUrl(job.id,'color-model.json'); colorReport.target = '_blank'; colorReport.rel = 'noopener';
      detailsTarget.append(colorReport,el('span','',' · '));
    }
    const report = el('a','text-button','Отчёт анализа JSON ↗'); report.href = fileUrl(job.id,'analysis.json'); report.target = '_blank'; report.rel = 'noopener';
    detailsTarget.append(report,el('span','',' · '));
  } else detailsTarget.append(el('p','notice warning','Старый пакет без смыслового анализа. Для нового анализа загрузите материалы повторно.'));
  const design = el('a','text-button','DESIGN.md ↗'); design.href = fileUrl(job.id,'DESIGN.md'); design.target = '_blank'; design.rel = 'noopener'; detailsTarget.append(design);
  const outline = analysis?.canonical_storyboard || analysis?.storyboard || [];
  if (outline.length) {
    const heading = el('h3','plan-heading','План презентации · '+slidesText(outline.length));
    heading.id = 'plan-heading'; heading.tabIndex = -1; $('#profile').append(heading);
    $('#profile').append(el('p','profile-detail plan-intro','Проверьте последовательность слайдов. При необходимости измените план перед генерацией.'));
    const list = el('ol','plan-outline');
    const overflow = el('ol','plan-outline'); overflow.style.counterReset = 'plan 4';
    for (const [index,slide] of outline.entries()) {
      const row=el('li'); row.append(el('strong','',slide.title));
      const claims=analysis?.editorial?.plan?.slides?.[index]?.bullets;
      if (claims?.length && index < 3) {
        const bullets=el('ul');
        for (const claim of claims.slice(0,2)) bullets.append(el('li','profile-detail',claim.text));
        row.append(bullets);
      }
      (index < 4 ? list : overflow).append(row);
    }
    $('#profile').append(list);
    if (overflow.children.length) {
      const more = el('details','plan-more');
      more.append(el('summary','',`Ещё ${slidesText(overflow.children.length)}`),overflow);
      $('#profile').append(more);
    }
  }
  if (analysis?.narrative) $('#profile').append(el('p','profile-detail',analysis.narrative.message));
  if (analysis?.editorial?.omitted?.length) {
    const details=el('details','selection-details');
    details.append(el('summary','',`Исключённые фрагменты: ${analysis.editorial.omitted.length}`));
    for (const item of analysis.editorial.omitted) details.append(el('p','profile-detail',item.source_text+' — '+item.explanation));
    detailsTarget.append(details);
  }
  const change = el('form','plan-revision');
  const number = el('input','form-control'); number.type='text'; number.inputMode='numeric';
  number.pattern='(?:[1-9]|1[0-9]|2[0-5])'; number.maxLength=2;
  number.value=outline.length || job.constraints.slides; number.required=true;
  number.id='plan-slide-count'; number.setAttribute('aria-label','Желаемое количество слайдов от 1 до 25');
  const slideLabel=el('label','field-label','Количество слайдов (1–25)');
  slideLabel.htmlFor=number.id;
  const slideError=el('small','field-error','Введите целое число от 1 до 25.');
  slideError.id='plan-slide-error'; slideError.hidden=true; slideError.setAttribute('role','alert');
  number.setAttribute('aria-describedby',slideError.id);
  const validSlideCount=() => /^(?:[1-9]|1[0-9]|2[0-5])$/.test(number.value);
  const validateSlideCount=() => {
    const valid=validSlideCount();
    number.setCustomValidity(valid ? '' : 'Введите целое число от 1 до 25.');
    number.setAttribute('aria-invalid',String(!valid)); slideError.hidden=valid;
    return valid;
  };
  number.addEventListener('input',validateSlideCount);
  const wishes = el('textarea','form-control'); wishes.maxLength=5000; wishes.rows=2;
  wishes.placeholder='Что выделить и что сократить'; wishes.setAttribute('aria-label','Пожелания к новому плану');
  const submit = el('button','secondary','Пересобрать план'); submit.type='submit';
  change.append(el('p','profile-detail','Изменим план под выбранное количество слайдов.'),slideLabel,number,slideError,wishes,submit);
  let cancellation = null;
  change.addEventListener('input',() => {
    if (autoGenerationPending) {
      if (!cancellation) cancellation=api(`/api/packages/${job.id}/auto-generation/cancel`,{method:'POST'});
      cancellation.catch(error => toast(error.message));
    }
    $('#auto-generation-status').textContent='Измените план и запустите генерацию после проверки.';
  });
  change.onsubmit=async event => {
    event.preventDefault(); if (busy || packageId!==job.id) return;
    if (!validateSlideCount()) { number.focus(); return; }
    const slides=Number(number.value); const instructions=wishes.value.trim() || 'Выделить главное, сократить текст и пересобрать план под выбранное количество слайдов.';
    stopAutoWatch(); setBusy(true); submit.disabled=true; beginPreparation('Заново выделяем главное из исходного текста');
    try { if (cancellation) await cancellation; await finishPreparation(await jsonPost(`/api/packages/${job.id}/revise`,{slides,instructions})); }
    catch (error) { preparationStopped(error); }
    finally { stopLoaders(); setBusy(false); submit.disabled=false; }
  };
  const planEdit = el('details','plan-edit'); planEdit.append(el('summary','','Изменить план'),change);
  if (slideBudget?.status === 'needs_input') {
    planEdit.open = true;
    $('#profile').append(el('p','notice warning','Материал не помещается в выбранный объём. Уточните план, чтобы продолжить.'));
  }
  $('#profile').append(planEdit);
  displayFontChanges(detailsTarget,analysis?.font_substitutions || t.font_substitutions);
  detailsTarget.append(journalButton(job.id));
  $('#prep-state').textContent = slideBudget?.status === 'needs_input' ? 'Нужно уточнить план' : 'Подготовлено';
  $('#prep-state').className = 'pill '+(slideBudget?.status === 'needs_input' ? 'neutral' : 'good');
  $('#step-2').classList.add('active'); $('#generate-button').disabled = busy || slideBudget?.status === 'needs_input';
  loadHistory();
  requestAnimationFrame(() => { $('#preparation-panel').scrollIntoView({behavior:'smooth',block:'start'}); $('#plan-heading')?.focus({preventScroll:true}); });
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
  activeHistoryId = job.id;
  document.body.classList.remove('analysis-ready','results-ready');
  syncNavState();
  $('#generation-panel').hidden = true; $('#analysis-details').hidden = true; $('#variant-guide').hidden = true;
  $('#profile-empty').hidden = true; $('#reuse-profile').hidden = true; $('#prep-loader').hidden = true; $('#profile').hidden = false;
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
  loadHistory();
}
$('#prepare-form').addEventListener('submit',async event => {
  event.preventDefault(); if (busy) return;
  if (!sourcePackageId && !$('#template').files.length) return toast('Загрузите шаблон PPTX или POTX.');
  if (!$('#content').value.trim()) return toast('Добавьте текст презентации.');
  // Disabled controls are omitted by FormData: collect before locking the UI.
  const data = new FormData(event.target);
  if (sourcePackageId) { data.delete('template'); data.set('source_package_id',sourcePackageId); }
  if (!$('#images').files.length) data.delete('images');
  if (!data.get('slides')) data.delete('slides');
  dirty(); setBusy(true); beginPreparation('Загрузка материалов');
  $('#prep-state').textContent = sourcePackageId ? 'Изучена' : 'Анализируем';
  $('#prep-state').className = 'pill '+(sourcePackageId ? 'good' : 'neutral');
  try {
    const job=await api('/api/prepare',{method:'POST',body:data});
    loadHistory();
    await finishPreparation(job);
  }
  catch (error) {
    preparationStopped(error);
  } finally { stopLoaders(); setBusy(false); }
});
async function startGeneration(nested = false) {
  if (!packageId || (busy && !nested)) return;
  stopAutoWatch(); autoGenerationPending = false; $("#cancel-auto-generation").hidden = true;
  $('#auto-generation-status').textContent = '';
  setBusy(true); $('#generation-panel').hidden = false; $('#generation-status').hidden = false; $('#results').hidden = true;
  clearGenerationError();
  setGenerationPhase('Передаём подготовленные материалы на генерацию');
  try {
    const job = await jsonPost('/api/generate',{package_id:packageId,variant_count:variantCount(),
      accept_adjusted_slide_count:slideBudget?.status === 'adjusted' || !!slideBudget?.confirm_plan});
    loadHistory();
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
  const generation = el('div'); generation.append(el('dt','',job.variants?.length === 1 ? 'Генерация одной презентации' : 'Генерация трёх презентаций'),el('dd','',duration(job.elapsed_seconds)));
  target.replaceChildren(preparation,generation);
  // Older results did not copy preparation timing; resolve their own package, not the editor's.
  if (job.analysis_seconds == null && job.package_id) {
    try {
      const source = await api(`/api/jobs/${encodeURIComponent(job.package_id)}`);
      if (target.dataset.jobId === job.id) analysis.textContent = duration(source.analysis_seconds);
    } catch { /* Missing historical preparation must not block downloads. */ }
  }
}
function reviewGroups(job) {
  const groups = new Map();
  const severityRank = {info:0,warning:1,error:2};
  for (const finding of job.quality_report?.findings || []) {
    const raw = String(finding.message || finding.code || 'Обнаружено замечание');
    const key = raw.trim();
    if (!groups.has(key)) groups.set(key,{code:finding.code,raw,variants:new Set(),slides:new Set(),severity:'info'});
    const group = groups.get(key);
    if (finding.variant) group.variants.add(finding.variant);
    if (Number(finding.slide) > 0) group.slides.add(Number(finding.slide));
    if ((severityRank[finding.severity] || 0) > severityRank[group.severity]) group.severity = finding.severity;
  }
  return [...groups.values()].map(group => {
    const variants = job.variants || [];
    const allVariants = group.variants.size === variants.length && variants.length > 0;
    const names = allVariants ? 'Все варианты' : [...group.variants].map(key => variants.find(v => v.key === key)?.title || key).join(', ');
    const slides = [...group.slides].sort((a,b) => a-b);
    const location = [names,slides.length ? `${slides.length === 1 ? 'слайд' : 'слайды'} ${slides.join(', ')}` : ''].filter(Boolean).join(' · ');
    const message = group.code === 'slide_count_adjusted' && Number.isInteger(job.slide_count_decision?.count)
      ? `Теперь в каждом варианте ${slidesText(job.slide_count_decision.count)}. Проверьте объём.`
      : /DeepPresenter/i.test(group.raw) ? 'Не удалось проверить оформление слайдов. Подробности — в журнале.' : group.code === 'archetype_generic_layout'
      ? 'Выбран общий макет. Проверьте, подходит ли оформление содержанию.' : group.raw;
    return {...group,text:location ? `${location}. ${message}` : message};
  });
}
function showResults(job) {
  // History results and the current editor have separate package identities.
  document.body.classList.add('results-ready'); document.body.classList.remove('analysis-ready');
  syncNavState();
  resultPackageId = job.package_id; diagnosticJob = job.id;
  activeHistoryId = job.id;
  clearTimeout(toastTimeout); $('#toast').hidden = true;
  const one = job.variants.length === 1;
  $('#results-title').textContent = one ? 'Ваша презентация' : 'Три варианта презентации';
  $('#results').hidden = false; $('#step-3').classList.add('active');
  $('#results').classList.toggle('single-variant',one);
  stopLoaders(); clearGenerationError(); showTimings(job);
  const slideCounts = job.variants.map(variant => variant.slides);
  const slideSummary = one ? slidesText(slideCounts[0]) : slideCounts.length === 0 ? '' : slideCounts.every(count => count === slideCounts[0])
    ? `по ${slidesText(slideCounts[0])}`
    : `всего ${slidesText(slideCounts.reduce((sum,count) => sum+count,0))}`;
  $('#result-summary').textContent = [states[job.state] || job.state,slideSummary].filter(Boolean).join(' · ');
  $('#result-summary').className = job.state === 'needs_review' ? 'status-warning' : '';
  $('#results').classList.toggle('needs-review',job.state === 'needs_review');
  $('#download-all').textContent = one ? 'Скачать ZIP ↓' : 'Скачать всё · ZIP ↓';
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
    card.append(button,el('h3','',variant.title),el('div','result-meta',`${slidesText(variant.slides)} · редактируемый PPTX`));
    const links = el('div','downloads');
    for (const format of ['pptx','pdf','html']) {
      const link = el('a','',format.toUpperCase()+' ↗'); link.href = fileUrl(job.id,`${variant.key}/deck.${format}`); link.target = '_blank'; link.rel = 'noopener'; link.setAttribute('aria-label',`${variant.title}: скачать ${format.toUpperCase()}`); links.append(link);
    }
    card.append(links); $('#result-grid').append(card);
  }
  const findingPriority = finding => finding.severity === 'error' ? 4
    : /безопасност|не проверены|проверки фона/i.test(finding.raw) ? 3
    : finding.severity === 'warning' ? 2 : 1;
  const findings = reviewGroups(job).sort((a,b) => findingPriority(b)-findingPriority(a));
  $('#result-alert').hidden = job.state !== 'needs_review'; $('#result-alert').replaceChildren();
  if (job.state === 'needs_review') {
    const alert = el('div','notice warning');
    alert.append(el('strong','',findings.length ? `${counted(findings.length,['замечание','замечания','замечаний'])} к результату` : 'Проверьте результат перед использованием'));
    if (findings.length) alert.append(el('p','',findings[0].text));
    if (findings.length) {
      const more = el('button','text-button','Все замечания →');
      more.onclick = () => { $('#audit-list').tabIndex = -1; $('#audit-list').scrollIntoView({behavior:'smooth',block:'start'}); $('#audit-list').focus({preventScroll:true}); };
      alert.append(more);
    }
    $('#result-alert').append(alert);
    $('#audit-list').append(el('p','profile-detail','Проверьте эти замечания в слайдах перед использованием.'));
    for (const finding of findings.slice(0,6)) $('#audit-list').append(el('p','audit-line '+finding.severity,finding.text));
    if (findings.length > 6) {
      const rest = el('details','review-more'); rest.append(el('summary','',`Ещё ${counted(findings.length - 6,['замечание','замечания','замечаний'])}`));
      for (const finding of findings.slice(6)) rest.append(el('p','audit-line '+finding.severity,finding.text));
      $('#audit-list').append(rest);
    }
  } else $('#audit-list').append(el('p','profile-detail','Автоматические проверки завершены. Подробности доступны в отчёте и журнале.'));
  $('#audit-list').append(journalButton(job.id));
  displayFontChanges($('#audit-list'),job.font_substitutions);
  loadHistory();
  requestAnimationFrame(() => { $('#results').scrollIntoView({behavior:'smooth',block:'start'}); $('#results h2').tabIndex=-1; $('#results h2').focus({preventScroll:true}); });
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
  if (busy) return; syncNavState(); setBusy(true);
  try {
    if (job.kind === 'preparation') { beginPreparation(job.phase || 'Анализируем материалы'); await finishPreparation(job); }
    else {
      clearGenerationError(); $('#results').hidden = true; $('#generation-panel').hidden = false; $('#generation-status').hidden = false;
      const done = await watchJob(job.id,state => {
        setGenerationPhase((state.slide_count_decision?.mode==='automatic' ? state.slide_count_decision.message+' ('+state.slide_count_decision.count+'). ' : '')+(state.phase || 'Получаем состояние генерации'));
      });
      if (['completed','needs_review'].includes(done.state)) showResults(done); else generationError(done.error || states[done.state]);
    }
  } catch (error) { if (job.kind === 'generation') generationError(error.message); else preparationStopped(error); }
  finally { stopLoaders(); setBusy(false); }
}
function syncNavState() {
  $('#nav-create').classList.toggle('active',!document.body.classList.contains('results-ready'));
}
let historyMenuAnchor=null;
function closeHistoryMenu(restoreFocus=false) {
  $('#history-menu').hidden=true;
  if (restoreFocus) historyMenuAnchor?.focus();
}
async function useHistoryPackage(preparation,generation,forNew=false) {
  if (busy) return;
  try {
    const selected=preparation || await api('/api/jobs/'+generation.package_id);
    if (selected.state!=='ready') throw Error('Пакет пока недоступен.');
    if (forNew) openReuseSetup(selected);
    else {
      displayProfile(selected);
      $('#workspace').scrollIntoView({behavior:'smooth',block:'start'});
    }
  } catch (error) { toast(error.message); }
}
function openReuseSetup(job) {
  $('#nav-create').click();
  configureReuseSource(job);
  $('#intro-title').scrollIntoView({behavior:'smooth',block:'start'});
}
function configureReuseSource(job) {
  sourcePackageId=job.id;
  sourceTemplateName=job.template.name;
  document.body.classList.add('reuse-ready');
  $('#template-field').hidden=true; $('#template').required=false;
  $('#reuse-source').hidden=false; $('#reuse-source-name').textContent=job.template.name;
  $('#prepare-button').replaceChildren('Подготовить план ',el('span','','→'));
  $('#intro-description').textContent=`Дизайн-система «${job.template.name}» уже изучена. Добавьте новый текст для презентации.`;
  $('#profile-empty').hidden=true; $('#reuse-profile').hidden=false;
  $('#prep-state').textContent='Изучена'; $('#prep-state').className='pill good';
  const template=job.template, profile=$('#reuse-profile'); profile.replaceChildren();
  profile.append(el('h3','',template.name),
    el('p','',`${slidesText(template.slide_count)} в шаблоне · ${counted(template.layout_count,['макет','макета','макетов'])}`));
  const colors=el('div','swatches');
  for (const color of (template.colors || []).slice(0,7)) {
    if (!/^#[0-9a-f]{6}$/i.test(color)) continue;
    const swatch=el('span','swatch'); swatch.style.backgroundColor=color;
    swatch.title=color; swatch.setAttribute('aria-label',`Цвет ${color}`); colors.append(swatch);
  }
  if (colors.childElementCount) profile.append(el('span','analysis-label','Основные цвета'),colors);
  const meta=el('div','reuse-profile-meta');
  if (template.font) meta.append(el('span','',`Шрифт: ${template.font}`));
  if (meta.childElementCount) profile.append(meta);
}
function showHistoryMenu(event,job,preparation,generation) {
  event.preventDefault();
  historyMenuAnchor=event.currentTarget;
  const menu=$('#history-menu'); menu.replaceChildren();
  const addAction=(label,action,danger=false) => {
    const item=el('button','history-menu-item'+(danger?' history-menu-item--danger':''),label);
    item.type='button'; item.setAttribute('role','menuitem');
    item.onclick=() => { closeHistoryMenu(); action(); };
    menu.append(item);
  };
  if (preparation?.state==='ready' || generation?.package_id)
    addAction('Сделать презентацию на основе',() => useHistoryPackage(preparation,generation,true));
  addAction('Журнал',() => openDiagnostics(job.id));
  addAction('Удалить',() => deleteHistoryEntry(preparation?.id || generation?.package_id || job.id),true);
  menu.hidden=false;
  const anchor=historyMenuAnchor.getBoundingClientRect();
  const x=event.type==='keydown' ? anchor.left+18 : event.clientX;
  const y=event.type==='keydown' ? anchor.top+18 : event.clientY;
  menu.style.left=Math.max(8,Math.min(x,window.innerWidth-menu.offsetWidth-8))+'px';
  menu.style.top=Math.max(8,Math.min(y,window.innerHeight-menu.offsetHeight-8))+'px';
  menu.querySelector('button')?.focus();
}
document.addEventListener('pointerdown',event => { if (!$('#history-menu').contains(event.target)) closeHistoryMenu(); });
document.addEventListener('keydown',event => { if (event.key==='Escape' && !$('#history-menu').hidden) closeHistoryMenu(true); });
window.addEventListener('scroll',closeHistoryMenu,true);
window.addEventListener('resize',closeHistoryMenu);
async function deleteHistoryEntry(id) {
  if (busy || !confirm('Удалить запуск, все его версии, презентации, пакет анализа и журналы? Восстановить их нельзя.')) return;
  try {
    const result=await api('/api/jobs/'+id,{method:'DELETE'});
    if (result.deleted.includes(packageId) || result.deleted.includes(resultPackageId) || result.deleted.includes(sourcePackageId)) {
      stopAutoWatch(); autoGenerationPending=false; packageId=null; resultPackageId=null;
      $('#nav-create').click();
    }
    await loadHistory();
  }
  catch (error) { toast(error.message); }
}
function renderHistory(jobs) {
  const list=$('#history-list'); list.replaceChildren();
  const preparations=new Map(jobs.filter(job=>job.kind==='preparation').map(job=>[job.id,job]));
  const pairedPackages=new Set(jobs.filter(job=>job.kind==='generation' && job.package_id).map(job=>job.package_id));
  if (!jobs.length) { list.append(el('p','history-empty','Пока нет запусков.')); return; }
  for (const job of jobs) {
    if (job.kind==='preparation' && pairedPackages.has(job.id)) continue;
    const generation=job.kind==='generation' ? job : null;
    const preparation=generation ? preparations.get(job.package_id) : job;
    const entry=el('div','history-entry'), row=el('button','history-item'), info=el('div','history-info');
    row.type='button';
    const title=preparation?.content?.title || preparation?.template_name || (generation?.variant_count === 1 ? 'Презентация' : generation ? 'Три презентации' : 'Подготовка шаблона');
    const name=el('strong','history-name',title); name.title=title;
    const date=new Date(job.created*1000).toLocaleDateString('ru',{day:'2-digit',month:'2-digit',year:'numeric'});
    const status=el('small','history-meta',`${date} · ${states[job.state] || job.state}`);
    info.append(name,status); row.append(info);
    if (activeHistoryId === job.id || activeHistoryId === preparation?.id) {
      row.classList.add('active'); row.setAttribute('aria-current','true');
    }
    const openLabel=generation && ['completed','needs_review'].includes(generation.state) ? 'Открыть презентацию'
      : preparation?.state==='ready' ? 'Открыть пакет'
      : ['accepted','running'].includes(job.state) ? 'Продолжить наблюдение'
      : preparation?.state==='waiting_fonts' ? 'Проверить шрифты' : 'Открыть журнал';
    row.setAttribute('aria-label',`${title}. ${states[job.state] || job.state}. ${openLabel}`);
    row.onclick=() => {
      if (busy) return;
      closeHistoryMenu();
      if (generation && ['completed','needs_review'].includes(generation.state)) showResults(generation);
      else if (preparation?.state==='ready' || generation?.package_id) useHistoryPackage(preparation,generation);
      else if (['accepted','running'].includes(job.state)) resumeJob(job);
      else if (preparation?.state==='waiting_fonts') displayMissingFonts(preparation);
      else openDiagnostics(job.id);
    };
    row.oncontextmenu=event => showHistoryMenu(event,job,preparation,generation);
    row.onkeydown=event => {
      if (event.key==='ContextMenu' || (event.shiftKey && event.key==='F10'))
        showHistoryMenu(event,job,preparation,generation);
    };
    const more=el('button','history-more','⋯');
    more.type='button'; more.setAttribute('aria-label',`Действия с запуском: ${title}`);
    more.onclick=event => showHistoryMenu(event,job,preparation,generation);
    entry.append(row,more); list.append(entry);
  }
}
async function loadHistory() {
  try {
    renderHistory(await api('/api/jobs'));
  } catch (error) { toast(error.message); }
}
$('#nav-create').onclick = () => {
  activeHistoryId=null;
  for (const row of document.querySelectorAll('#history-list .history-item.active')) {
    row.classList.remove('active'); row.removeAttribute('aria-current');
  }
  dirty(); $('#prepare-form').reset(); $('#template').value = ''; $('#images').value = '';
  setVariantCount(3);
  clearReuseSource();
  $('#file-label').textContent = uploadPrompt; $('#char-count').textContent = '0 символов'; $('#images-list').replaceChildren();
  $('#profile-empty h3').textContent = 'Шаблон ещё не загружен';
  $('#profile-empty p').textContent = 'После анализа здесь появятся его шрифты, цвета и макеты.';
  $('#extra-settings').open = false;
  $('#profile').hidden=true; $('#profile-empty').hidden=false; $('#variant-guide').hidden=false;
  $('#generation-panel').hidden=true; $('#analysis-details').hidden=true; $('#prep-loader').hidden=true;
  document.body.classList.remove('results-ready','analysis-ready');
  $('#nav-create').classList.add('active');
  $('#results').hidden = true;
  $('#intro-title').textContent = 'Новая презентация';
  $('#intro-description').textContent = 'Загрузите шаблон и добавьте текст. После анализа выберите одну или три презентации.';
  $('#workspace').scrollIntoView({behavior:'smooth',block:'start'});
};
$('#new-presentation').onclick = () => $('#nav-create').click();
$('#edit-materials').onclick = () => { dirty(); (sourcePackageId ? $('#content') : $('#template')).focus(); $('#workspace').scrollIntoView({behavior:'smooth',block:'start'}); };
$('#demo').onclick = async () => {
  if (busy) return;
  try {
    const response = await fetch('/static/demo.md'); if (!response.ok) throw Error('Демо недоступно');
    $('#content').value = await response.text(); $('#content').dispatchEvent(new Event('input'));
    dirty(); toast('Демотекст загружен. Данные вымышлены.','info');
  } catch (error) { toast(error.message); }
};
async function updateRuntime() {
  try {
    const runtime = await api('/api/runtime');
    $('#runtime-status').textContent = runtime.restart_required
      ? 'Для применения обновлений перезапустите сервер.' : '';
  } catch (_) { $('#runtime-status').textContent = 'Статус сервера временно недоступен.'; }
}

let autoWatch = null, diagnosticJob = null, logWatch = null;
function stopAutoWatch() { clearTimeout(autoWatch); autoWatch = null; }
function armAutoGeneration(job) {
  stopAutoWatch(); diagnosticJob = job.id;
  autoGenerationPending = job.auto_generation === 'scheduled';
  const pid = job.id;
  async function tick() {
    if (packageId !== pid || securityLocked) return;
    try {
      const current = await api('/api/jobs/'+pid);
      if (packageId !== pid) return;
      const status = $('#auto-generation-status');
      $('#cancel-auto-generation').hidden = current.auto_generation !== 'scheduled';
      autoGenerationPending = current.auto_generation === 'scheduled';
      if (current.generation_id) {
        status.textContent = 'Генерация запущена.';
        if (!busy) await resumeJob(await api('/api/jobs/'+current.generation_id));
        else autoWatch = setTimeout(tick,1000);
        return;
      }
      if (current.auto_generation === 'scheduled') {
        const seconds = Math.max(0,Math.ceil(current.auto_generate_at-Date.now()/1000));
        status.textContent = seconds ? `Через ${seconds} сек. начнётся создание трёх презентаций. Можно изменить план или отменить автозапуск.` : 'Сервер запускает генерацию…';
      } else if (current.auto_generation === 'needs_confirmation') {
        status.textContent = 'Проверьте структуру и количество слайдов. Подтвердите план кнопкой генерации или измените его.';
        return;
      } else if (['cancelled','blocked'].includes(current.auto_generation)) {
        status.textContent = current.auto_generation === 'cancelled' ? 'Автозапуск отменён. Можно запустить вручную.' : 'Автозапуск не выполнен. Подробности — в журнале задания.';
        return;
      } else if (!current.auto_generation || current.auto_generation === 'manual') {
        status.textContent = 'Проверьте план и выберите, сколько презентаций создать.';
        return;
      }
    } catch (_) { $('#auto-generation-status').textContent = 'Нет связи. Серверный автозапуск не отменён; проверяем состояние…'; }
    autoWatch = setTimeout(tick,1000);
  }
  autoWatch = setTimeout(tick,250);
}
$('#cancel-auto-generation').onclick = async () => {
  if (!packageId) return;
  try { await api('/api/packages/'+packageId+'/auto-generation/cancel',{method:'POST'}); autoGenerationPending = false; }
  catch (error) { toast(error.message); }
};
async function openDiagnostics(id) {
  clearTimeout(logWatch);
  const dialog = $('#diagnostics-dialog');
  if (!dialog.open) dialog.showModal();
  $('#diagnostics-title').textContent = 'Журнал задания '+id.slice(0,8);
  $('#diagnostics-title').focus({preventScroll:true});
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
$('#close-diagnostics').onclick = () => $('#diagnostics-dialog').close();
$('#diagnostics-dialog').addEventListener('close',() => clearTimeout(logWatch));

async function init() {
  try {
    const health = await api('/api/health');
    if (health.engine === 'deeppresenter') {
      if (!health.deeppresenter?.ready) toast('Движок генерации не готов. Проверьте зависимости на сервере.');
    }
    if (health.engine === 'pptagent_v02') {
      if (!health.pptagent?.ready) toast('Движок генерации не готов. Проверьте настройки сервера.');
    }
    $('#model-disclosure').textContent = health.model_mode === 'api'
      ? 'При анализе модель получает текст и параметры макетов. При включённой визуальной проверке отрисованные изображения готовых слайдов также отправляются настроенному провайдеру модели. Исходный файл PPTX / POTX не отправляется. Внешние ссылки не открываются.'
      : 'Автономный режим: материалы обрабатываются локально, без смыслового анализа LLM. Внешние ссылки не открываются.';
    if (health.model_mode==='api' && !health.features?.vlm) $('#model-disclosure').textContent += ' Проверка изображений сейчас отключена; её включение требует разрешения на передачу PNG.';
    if (health.features?.vlm) $('#model-disclosure').textContent += ' Загруженные вами картинки также будут видны провайдеру в составе слайдов, но не передаются планировщику.';
    if (health.features?.download_fonts) $('#model-disclosure').textContent += ' Недостающие начертания ищем в Google Fonts, Fontsource и официальном пакете Aptos: передаётся только название шрифта.';
    const jobs = await api("/api/jobs");
    renderHistory(jobs);
    const active = jobs.find(job => ["accepted","running"].includes(job.state));
    const pending = jobs.find(job => job.state === "ready" && job.auto_generation === "scheduled");
    if (!busy && !packageId && !securityLocked) {
      if (active) resumeJob(active);
      else if (pending) displayProfile(pending);
    }
    // Never silently substitute an organizer example for a user upload.
  } catch (error) { toast(error.message); }
}
init();
try { if (sessionStorage.getItem('studio-security-blocked') === '1') lockForInjection(); } catch (_) { /* No persistent storage. */ }
updateRuntime();
setInterval(() => { if (!document.hidden) updateRuntime(); }, 10000);

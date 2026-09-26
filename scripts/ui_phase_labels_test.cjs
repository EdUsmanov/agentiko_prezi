// Pure formatter regression; no browser, server or model requests.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
const context=vm.createContext({});
vm.runInContext(source.slice(source.indexOf('const phaseLabels ='),source.indexOf('function setPreparationPhase')),context);
for (const message of [
  'Макеты native-slide-13: Повторяем незавершённый блок анализа: ValueError',
  'OpenRouter: HTTPError',
  'Ошибка при проверке дизайна',
  'InputRejected',
  'Генерация: не удалось проверить ответ'
]) assert.equal(context.humanPhase(message),'Уточняем результат проверки');
assert.equal(context.humanPhase('Проверено макетов 7 из 31'),'Проверено макетов 7 из 31');
assert.equal(context.humanPhase('Вёрстка, аудит и экспорт'),'Размещаем текст и таблицы на слайдах');
console.log('7 phase-label checks passed');

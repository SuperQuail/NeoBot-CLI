const sdk = window.NeoBotPlugin;
const button = document.getElementById('greet');
const nameInput = document.getElementById('name');
const output = document.getElementById('result');
let context;
let pending = false;

function update() {
  const allowed = context?.permissions?.some((item) => ['read', 'operate', 'manage'].includes(item));
  button.disabled = pending || !context?.available || context?.suspended || !allowed;
}

const unsubscribe = sdk.onContext((next) => {
  context = next;
  update();
});
const unregister = sdk.registerAnchor('preview', document.getElementById('preview'));
button.addEventListener('click', async () => {
  pending = true;
  update();
  try {
    const result = await sdk.action('greeting', { name: nameInput.value });
    output.textContent = result.text;
  } catch (error) {
    await sdk.notify({ title: '预览失败', message: error.message, tone: 'err' });
  } finally {
    pending = false;
    update();
  }
});
sdk.ready().then((next) => { context = next; update(); }).catch(() => {
  output.textContent = '请从 NeoBot 插件页面打开。';
});
window.addEventListener('pagehide', () => { unsubscribe(); unregister(); }, { once: true });

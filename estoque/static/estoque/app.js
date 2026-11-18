(function () {
  var root = document.documentElement;
  var btn = document.getElementById('theme-toggle');
  if (btn) btn.addEventListener('click', function () {
    var atual = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', atual);
    try { localStorage.setItem('tema', atual); } catch (e) {}
  });
  var menu = document.getElementById('menu-toggle');
  if (menu) {
    menu.addEventListener('click', function () {
      var aberto = document.body.classList.toggle('nav-open');
      menu.setAttribute('aria-expanded', aberto ? 'true' : 'false');
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { document.body.classList.remove('nav-open'); menu.setAttribute('aria-expanded', 'false'); }
    });
    document.addEventListener('click', function (e) {
      if (document.body.classList.contains('nav-open') && !e.target.closest('.sidebar') && !e.target.closest('#menu-toggle')) {
        document.body.classList.remove('nav-open');
      }
    });
  }
  // confirmação em ações destrutivas
  document.addEventListener('submit', function (e) {
    var msg = e.target.getAttribute('data-confirm');
    if (msg && !window.confirm(msg)) e.preventDefault();
  });
  // formset dinâmico (itens do pedido)
  var add = document.getElementById('add-item');
  if (add) {
    add.addEventListener('click', function () {
      var total = document.getElementById('id_itens-TOTAL_FORMS') || document.querySelector('input[name$="-TOTAL_FORMS"]');
      var tpl = document.getElementById('empty-form').innerHTML.replace(/__prefix__/g, total.value);
      document.getElementById('itens-body').insertAdjacentHTML('beforeend', tpl);
      total.value = parseInt(total.value, 10) + 1;
    });
  }
  // preencher custo ao escolher produto no pedido
  document.addEventListener('change', function (e) {
    if (e.target.matches && e.target.matches('select[name$="-produto"]')) {
      var opt = e.target.selectedOptions[0];
      var custo = opt && opt.getAttribute('data-custo');
      var campo = document.getElementById(e.target.id.replace('-produto', '-custo_unitario'));
      if (custo && campo && !campo.value) campo.value = custo;
    }
  });
})();

(() => {
  "use strict";
  const $ = (s, raiz = document) => raiz.querySelector(s);
  const $$ = (s, raiz = document) => Array.from(raiz.querySelectorAll(s));
  const mmss = (s) => `${Math.floor(Math.max(0, s) / 60)}:${String(Math.max(0, s) % 60).padStart(2, "0")}`;
  const solo = (t) => (t || "").replace(/\D/g, "");
  const titulo = (t) => t.toLowerCase().replace(/(^|\s)\S/g, (c) => c.toUpperCase());
  const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  function texto(el, t) { if (el && el.textContent !== t) el.textContent = t; }
  function oculto(el, h) { if (el && el.hidden !== h) el.hidden = h; }

  async function api(url, opciones = {}) {
    const r = await fetch(url, {
      headers: {"Content-Type": "application/json"}, ...opciones,
      signal: AbortSignal.timeout ? AbortSignal.timeout(10000) : undefined,
    });
    let datos = {};
    try { datos = await r.json(); } catch (_) {}
    return {status: r.status, datos};
  }

  // ================= Monitores =================
  const vistos = new Set();
  let primeraCarga = true;
  let estadoCajas = {};       // slug -> {reservada, restante_seg}
  let cajasRecibido = 0;

  function filaPago(p, conCaja) {
    const nuevo = !primeraCarga && !vistos.has(p.id + (conCaja ? "c" : ""));
    vistos.add(p.id + (conCaja ? "c" : ""));
    const confirmado = p.segundos ? `✓ Confirmado en ${p.segundos} s` : "✓ Confirmado";
    return `<li class="pago${nuevo ? " nuevo" : ""}">
      <span class="pago-monto">S/ ${esc(p.monto)}</span>
      <span class="pago-linea">${conCaja ? `<span class="pago-caja">${esc(p.caja_nombre)}</span>` : ""}${esc(p.hora)} · ${esc(p.medio)}</span>
      <span class="pago-ok">${confirmado}</span></li>`;
  }

  function pintarLista(el, pagos, conCaja) {
    const clave = pagos.map((p) => p.id).join(",");
    if (el.dataset.clave === clave) return;
    el.dataset.clave = clave;
    el.innerHTML = pagos.length ? pagos.map((p) => filaPago(p, conCaja)).join("")
      : '<li class="vacio">Aún no hay pagos hoy.</li>';
  }

  function pintarCajas() {
    const extra = Math.floor((performance.now() - cajasRecibido) / 1000);
    for (const [slug, c] of Object.entries(estadoCajas)) {
      const mon = $(`.monitor[data-caja="${slug}"]`);
      const restante = c.restante_seg - extra;
      const reservada = c.reservada && restante > -15;
      const etiqueta = $("[data-estado]", mon);
      texto(etiqueta, reservada ? `Reservada · ${mmss(restante)}` : "Libre");
      etiqueta.classList.toggle("reservada", reservada);
      etiqueta.classList.toggle("libre", !reservada);
      const boton = $("[data-pagar]", mon);
      boton.disabled = reservada;
      texto(boton, reservada ? "Caja ocupada, intenta en un momento" : "Pagar en esta caja");
    }
  }

  async function refrescarMonitores() {
    try {
      const {datos} = await api("/api/clinica/estado");
      if (!datos.cajas) return;
      estadoCajas = {};
      for (const c of datos.cajas) {
        estadoCajas[c.slug] = c;
        pintarLista($(`.monitor[data-caja="${c.slug}"] [data-pagos]`), c.pagos, false);
      }
      cajasRecibido = performance.now();
      pintarCajas();
      const conta = datos.contabilidad;
      texto($("#conta-total"), conta.total);
      texto($("#conta-cantidad"), conta.cantidad === 1 ? "1 pago" : `${conta.cantidad} pagos`);
      pintarLista($("#conta-pagos"), conta.pagos, true);
      primeraCarga = false;
    } catch (_) { /* la próxima vuelta reintenta */ }
  }

  // ================= Panel del visitante =================
  const CAJAS = {};
  $$(".monitor[data-caja]").forEach((m) => { CAJAS[m.dataset.caja] = $("h2", m).textContent; });
  let cajaElegida = null;
  let demoId = new URLSearchParams(location.search).get("d");
  let ultimo = null;
  let recibido = 0;

  function vista(nombre) {
    ["datos", "pago", "listo", "fin"].forEach((v) => oculto($("#v-" + v), v !== nombre));
  }
  function abrirPanel() { oculto($("#panel"), false); document.body.style.overflow = "hidden"; }
  function cerrarPanel() {
    oculto($("#panel"), true);
    document.body.style.overflow = "";
    if (ultimo && ["aceptado", "expirado", "cancelado", "rechazado", "error_emision"].includes(ultimo.estado)) olvidar();
  }
  function olvidar() {
    demoId = null; ultimo = null;
    history.replaceState(null, "", location.pathname);
  }
  function errores(map) {
    $$("[data-error]").forEach((el) => { el.textContent = (map || {})[el.dataset.error] || ""; });
  }

  $$("[data-pagar]").forEach((b) => b.addEventListener("click", () => {
    if (demoId && ultimo && ultimo.estado === "esperando_pago") { abrirPanel(); return; }
    olvidar();
    cajaElegida = b.dataset.pagar;
    texto($("#panel-caja"), `Pagar en ${CAJAS[cajaElegida]}`);
    errores({});
    texto($("#error-general"), "");
    vista("datos");
    abrirPanel();
    $("#documento").focus();
  }));
  $("#btn-cerrar").addEventListener("click", cerrarPanel);
  $("#panel").addEventListener("click", (e) => { if (e.target.id === "panel") cerrarPanel(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#panel").hidden) cerrarPanel(); });

  // ---- datos del comprobante ----
  const form = $("#form-caja");
  const tipoActual = () => form.querySelector("input[name=tipo]:checked").value;
  let ultimoDoc = "";

  form.querySelectorAll("input[name=tipo]").forEach((r) => r.addEventListener("change", () => {
    const factura = tipoActual() === "factura";
    texto($("#lbl-documento"), factura ? "RUC" : "DNI");
    $("#documento").placeholder = factura ? "11 dígitos" : "8 dígitos";
    texto($("#lbl-nombre"), factura ? "Razón social" : "Tu nombre");
    $("#nombre").placeholder = factura ? "Se completa con el RUC" : "Se completa con tu DNI";
    $$(".solo-factura").forEach((el) => { el.hidden = !factura; });
    texto($("#estado-doc"), "");
    ultimoDoc = "";
    errores({});
    consultarDoc();
  }));

  async function consultarDoc() {
    const n = solo($("#documento").value);
    const factura = tipoActual() === "factura";
    if (n.length !== (factura ? 11 : 8) || n === ultimoDoc) return;
    ultimoDoc = n;
    const estado = $("#estado-doc");
    texto(estado, factura ? "Consultando SUNAT…" : "Consultando RENIEC…");
    try {
      const {datos} = await api(`/api/consulta/${factura ? "ruc" : "dni"}/${n}`);
      if (solo($("#documento").value) !== n) return;
      if (datos.ok) {
        $("#nombre").value = datos.nombre || "";
        if (factura) $("#direccion").value = datos.direccion || "";
        const extra = factura ? [datos.estado, datos.condicion].filter(Boolean).join(" · ") : "";
        texto(estado, "✓ Encontrado" + (extra ? ` (${extra})` : ""));
        if (!factura && !$("#titular").value.trim()) $("#titular").value = titulo(datos.nombre || "");
      } else {
        texto(estado, datos.error || "No pudimos consultarlo. Escríbelo a mano.");
        ultimoDoc = "";
      }
    } catch (_) {
      texto(estado, "No pudimos consultarlo. Escríbelo a mano.");
      ultimoDoc = "";
    }
  }
  $("#documento").addEventListener("input", consultarDoc);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errores({});
    texto($("#error-general"), "");
    const btn = $("#btn-voy");
    btn.disabled = true;
    texto(btn, "Reservando la caja…");
    const cuerpo = {
      caja: cajaElegida, tipo: tipoActual(), documento: solo($("#documento").value),
      nombre: $("#nombre").value, direccion: $("#direccion").value, titular: $("#titular").value,
      whatsapp: $("#whatsapp") ? $("#whatsapp").value : "",
    };
    try {
      const {status, datos} = await api("/api/clinica/reservar", {method: "POST", body: JSON.stringify(cuerpo)});
      if (datos.ok) {
        demoId = datos.id;
        history.replaceState(null, "", `?d=${demoId}`);
        await refrescarDemo();
        refrescarMonitores();
      } else if (datos.errores) {
        errores(datos.errores);
        const campo = document.getElementById(Object.keys(datos.errores)[0]);
        if (campo) campo.focus();
      } else {
        texto($("#error-general"), datos.error || `No pudimos reservar la caja (error ${status}). Intenta otra vez.`);
        if (datos.ocupada) refrescarMonitores();
      }
    } catch (_) {
      texto($("#error-general"), "Sin conexión. Revisa tu internet e intenta otra vez.");
    } finally {
      btn.disabled = false;
      texto(btn, "Voy a pagar");
    }
  });

  // ---- pago ----
  $("#btn-copiar").addEventListener("click", async (e) => {
    const b = e.currentTarget;
    try { await navigator.clipboard.writeText(b.dataset.numero); } catch (_) {
      const t = document.createElement("textarea");
      t.value = b.dataset.numero; document.body.appendChild(t); t.select();
      document.execCommand("copy"); t.remove();
    }
    texto(b, "¡Copiado!");
    setTimeout(() => texto(b, "Copiar"), 1800);
  });

  $("#btn-cancelar").addEventListener("click", async () => {
    if (!demoId) return;
    try { await api(`/api/clinica/${demoId}/cancelar`, {method: "POST"}); } catch (_) {}
    await refrescarDemo();
    refrescarMonitores();
  });
  $("#btn-reintentar").addEventListener("click", () => { olvidar(); cerrarPanel(); });
  $("#btn-otra").addEventListener("click", () => { olvidar(); cerrarPanel(); });

  function pintarDemo(d) {
    $$(".caja-nombre").forEach((el) => texto(el, d.caja_nombre || ""));
    if (d.estado === "esperando_pago") {
      vista("pago");
      // Reserva y detección están desacopladas: al terminar la reserva se oculta el reloj, pero
      // seguimos buscando el pago. Así quien tarda en volver de Yape igual se confirma.
      const reservaViva = (d.reserva_restante_seg || 0) > 0;
      oculto($(".reloj-reserva"), !reservaViva);
      texto($("#pago-estado"), d.mensaje || (reservaViva
        ? "Esperando tu pago…"
        : "Seguimos buscando tu pago… apenas llegue, se confirma solo."));
      return;
    }
    if (["expirado", "cancelado"].includes(d.estado)) {
      vista("fin");
      texto($("#fin-texto"), d.mensaje || "La reserva terminó. La caja quedó libre.");
      return;
    }
    vista("listo");
    texto($("#listo-seg"), String(d.confirmado_seg ?? "—"));
    texto($("#listo-medio"), d.medio || "Yape / Plin");
    const listo = d.estado === "aceptado";
    const fallo = d.estado === "rechazado" || d.estado === "error_emision";
    const nombreComp = `${d.tipo === "factura" ? "Factura" : "Boleta"} ${d.numero_formato || ""}`.trim();
    texto($("#comp-estado"), listo ? `✓ ${nombreComp} aceptada por SUNAT`
      : fallo ? (d.mensaje || "No se pudo emitir el comprobante.")
      : (d.mensaje || (["pago_confirmado", "emitiendo"].includes(d.estado)
        ? "Emitiendo tu comprobante…" : "Esperando la respuesta de SUNAT…")));
    oculto($("#comp-acciones"), !(listo && d.pdf));
    if (listo && d.pdf) { $("#btn-pdf").href = d.pdf; $("#btn-descargar").href = d.descargar; }
  }

  function relojReserva() {
    if (!ultimo || ultimo.estado !== "esperando_pago") return;
    const restante = ultimo.reserva_restante_seg - Math.floor((performance.now() - recibido) / 1000);
    if (restante <= 0) return;   // reserva terminada: el panel ya muestra "seguimos buscando"
    texto($("#reserva-reloj"), mmss(restante));
    $(".reloj-reserva").classList.toggle("poco", restante <= 30);
  }

  async function refrescarDemo() {
    if (!demoId) return;
    try {
      const {status, datos} = await api(`/api/demos/${demoId}`);
      if (status === 404 || (datos.ok && !datos.caja)) { olvidar(); return; }
      if (!datos.ok) return;
      const antes = ultimo && ultimo.estado;
      ultimo = datos;
      recibido = performance.now();
      if ($("#panel").hidden && !antes) abrirPanel();      // al volver con ?d= se reabre el panel
      pintarDemo(datos);
      relojReserva();
    } catch (_) { /* reintenta */ }
  }

  setInterval(() => { pintarCajas(); relojReserva(); }, 250);
  (async function bucleMonitores() {
    await refrescarMonitores();
    setTimeout(bucleMonitores, 2000);
  })();
  (async function bucleDemo() {
    const activo = ultimo && ["esperando_pago", "pago_confirmado", "emitiendo", "enviando"].includes(ultimo.estado);
    if (demoId && (activo || !ultimo)) await refrescarDemo();
    setTimeout(bucleDemo, activo && ultimo.estado === "esperando_pago" ? 1500 : 1000);
  })();
})();

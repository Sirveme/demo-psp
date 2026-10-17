(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const RUBRO = window.DEMO.rubro;

  let demoId = new URLSearchParams(location.search).get("d");
  let ultimo = null;            // última respuesta del servidor
  let recibido = 0;             // performance.now() de esa respuesta
  let animado = false;
  let consultando = false;

  // ---------- utilidades ----------
  const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.max(0, s) % 60).padStart(2, "0")}`;
  const solo = (t) => (t || "").replace(/\D/g, "");

  async function api(url, opciones = {}) {
    const r = await fetch(url, {
      headers: {"Content-Type": "application/json"}, ...opciones,
      signal: AbortSignal.timeout ? AbortSignal.timeout(10000) : undefined,
    });
    let datos = {};
    try { datos = await r.json(); } catch (_) {}
    return {status: r.status, datos};
  }

  // Cambia de paso solo si el paso cambia: los refrescos periódicos nunca mueven el scroll ni el foco.
  let pasoActual = 0;
  function mostrarPaso(n) {
    if (n === pasoActual) return;
    pasoActual = n;
    [1, 2, 3].forEach((i) => { $("#paso" + i).hidden = i !== n; });
    $$(".pasos li").forEach((li) => {
      const p = Number(li.dataset.paso);
      li.classList.toggle("activo", p === n);
      li.classList.toggle("hecho", p < n);
    });
    window.scrollTo({top: 0});
  }

  // Escribe solo si el contenido cambió (no toca el DOM en cada consulta).
  function texto(el, t) { if (el.textContent !== t) el.textContent = t; }
  function oculto(el, h) { if (el.hidden !== h) el.hidden = h; }

  function errores(map, atributo = "data-error") {
    $$(`[${atributo}]`).forEach((el) => { el.textContent = (map || {})[el.getAttribute(atributo)] || ""; });
  }

  // ---------- Paso 1 ----------
  const form = $("#form-datos");
  const tipoActual = () => form.querySelector("input[name=tipo]:checked").value;

  function ajustarTipo() {
    const factura = tipoActual() === "factura";
    $("#lbl-documento").innerHTML = factura ? "RUC" : "DNI <small>(opcional)</small>";
    $("#documento").placeholder = factura ? "11 dígitos" : "8 dígitos";
    $("#lbl-nombre").textContent = factura ? "Razón social" : "Tu nombre";
    $("#nombre").placeholder = factura ? "Se completa con el RUC" : "Como quieres que salga en la boleta";
    $$(".solo-factura").forEach((el) => { el.hidden = !factura; });
    $("#estado-doc").textContent = "";
    errores({});
    consultarDoc();
  }
  form.querySelectorAll("input[name=tipo]").forEach((r) => r.addEventListener("change", ajustarTipo));

  let ultimoDoc = "";
  async function consultarDoc() {
    const n = solo($("#documento").value);
    const factura = tipoActual() === "factura";
    const largo = factura ? 11 : 8;
    if (n.length !== largo || n === ultimoDoc) return;
    ultimoDoc = n;
    const estado = $("#estado-doc");
    estado.textContent = factura ? "Consultando SUNAT…" : "Consultando RENIEC…";
    try {
      const {datos} = await api(`/api/consulta/${factura ? "ruc" : "dni"}/${n}`);
      if (solo($("#documento").value) !== n) return;
      if (datos.ok) {
        $("#nombre").value = datos.nombre || "";
        if (factura) {
          $("#direccion").value = datos.direccion || "";
          const extra = [datos.estado, datos.condicion].filter(Boolean).join(" · ");
          estado.textContent = "✓ Encontrado" + (extra ? ` (${extra})` : "");
        } else {
          estado.textContent = "✓ Encontrado";
          if (!$("#titular").value.trim()) $("#titular").value = titulo(datos.nombre || "");
        }
      } else {
        estado.textContent = datos.error || "No pudimos consultarlo. Escríbelo a mano.";
        ultimoDoc = "";
      }
    } catch (_) {
      estado.textContent = "No pudimos consultarlo. Escríbelo a mano.";
      ultimoDoc = "";
    }
  }
  const titulo = (t) => t.toLowerCase().replace(/(^|\s)\S/g, (c) => c.toUpperCase());
  $("#documento").addEventListener("input", () => {
    const n = solo($("#documento").value);
    if (n.length < (tipoActual() === "factura" ? 11 : 8)) $("#estado-doc").textContent = "";
    consultarDoc();
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errores({});
    $("#error-general").textContent = "";
    const btn = $("#btn-continuar");
    btn.disabled = true;
    btn.textContent = "Registrando…";
    const cuerpo = {
      rubro: RUBRO,
      tipo: tipoActual(),
      documento: solo($("#documento").value),
      nombre: $("#nombre").value,
      direccion: $("#direccion").value,
      titular: $("#titular").value,
      monto: $("#monto").value,
      billetera: form.querySelector("input[name=billetera]:checked").value,
    };
    try {
      const {status, datos} = await api("/api/demos", {method: "POST", body: JSON.stringify(cuerpo)});
      if (datos.ok) {
        demoId = datos.id;
        history.replaceState(null, "", `?d=${demoId}`);
        animado = false;
        await refrescar();
      } else if (datos.errores) {
        errores(datos.errores);
        const primero = Object.keys(datos.errores)[0];
        const campo = document.getElementById(primero);
        if (campo) campo.focus();
      } else {
        $("#error-general").textContent = datos.error || `No pudimos registrar tus datos (error ${status}). Intenta otra vez.`;
      }
    } catch (_) {
      $("#error-general").textContent = "Sin conexión. Revisa tu internet e intenta otra vez.";
    } finally {
      btn.disabled = false;
      btn.textContent = "Continuar al pago";
    }
  });

  // ---------- Paso 2 ----------
  $("#btn-copiar").addEventListener("click", async (e) => {
    const b = e.currentTarget;
    try {
      await navigator.clipboard.writeText(b.dataset.numero);
    } catch (_) {
      const t = document.createElement("textarea");
      t.value = b.dataset.numero; document.body.appendChild(t); t.select();
      document.execCommand("copy"); t.remove();
    }
    b.textContent = "¡Copiado!";
    setTimeout(() => { b.textContent = "Copiar"; }, 1800);
  });

  // Hora enviada que el servidor aún no confirma: mientras tanto se muestra "Buscando…".
  let voucherEnviado = null;
  function estadoVoucher(t, ok = false) {
    const el = $("#voucher-estado");
    texto(el, t || "");
    oculto(el, !t);
    el.classList.toggle("ok", ok);
  }

  $("#form-voucher").addEventListener("submit", async (e) => {
    e.preventDefault();
    const hora = $("#voucher-hora").value;
    texto($("#voucher-error"), "");
    if (!/^\d{2}:\d{2}$/.test(hora)) { texto($("#voucher-error"), "Escribe la hora como 14:05."); return; }
    voucherEnviado = hora;
    estadoVoucher(`Buscando tu pago a las ${hora}…`);
    const btn = $("#btn-voucher");
    btn.disabled = true;
    try {
      const {datos} = await api(`/api/demos/${demoId}/voucher`, {method: "POST", body: JSON.stringify({hora})});
      if (!datos.ok) {
        voucherEnviado = null;
        estadoVoucher("");
        texto($("#voucher-error"), datos.error || "No pudimos guardar la hora. Intenta otra vez.");
      }
    } catch (_) {
      voucherEnviado = null;
      estadoVoucher("");
      texto($("#voucher-error"), "Sin conexión. Intenta otra vez.");
    } finally {
      btn.disabled = false;
    }
  });

  $("#btn-reintentar").addEventListener("click", () => {
    demoId = null;
    history.replaceState(null, "", location.pathname);
    ultimo = null;
    mostrarPaso(1);
  });

  // ---------- Paso 3 ----------
  $("#form-interes").addEventListener("submit", async (e) => {
    e.preventDefault();
    errores({}, "data-error-i");
    const cuerpo = {
      demo_id: demoId, rubro: RUBRO,
      nombre: $("#i-nombre").value, celular: $("#i-celular").value, ruc: $("#i-ruc").value,
      acepta: $("#i-acepta").checked,
    };
    const btn = e.submitter || $("#form-interes button");
    btn.disabled = true;
    try {
      const {datos} = await api("/api/interesados", {method: "POST", body: JSON.stringify(cuerpo)});
      if (datos.ok) {
        $("#i-ok").hidden = false;
        window.location.href = datos.whatsapp;
      } else {
        errores(datos.errores || {acepta: datos.error || "No pudimos guardar tus datos."}, "data-error-i");
      }
    } catch (_) {
      errores({acepta: "Sin conexión. Intenta otra vez."}, "data-error-i");
    } finally {
      btn.disabled = false;
    }
  });

  // ---------- Estado ----------
  function pintar(d) {
    const enPago = d.estado === "esperando_pago" || d.estado === "expirado";
    if (enPago) {
      mostrarPaso(2);
      texto($("#p2-monto"), d.monto);
      texto($("#p2-titular"), d.titular);
      const expirado = d.estado === "expirado";
      oculto($("#p2-estado"), expirado);
      oculto($("#p2-expirado"), !expirado);
      oculto($("#form-voucher"), expirado);
      const voucherServidor = d.voucher_hora;
      // El mensaje del servidor va junto al campo de hora si hay hora; si no, bajo el estado.
      if (voucherEnviado && voucherServidor !== voucherEnviado) {
        estadoVoucher(`Buscando tu pago a las ${voucherEnviado}…`);
      } else if (voucherServidor) {
        voucherEnviado = null;
        estadoVoucher(d.mensaje || `Buscando tu pago a las ${voucherServidor}…`);
      }
      const msg = $("#p2-mensaje");
      const general = voucherServidor ? "" : (d.mensaje || "");
      texto(msg, general);
      oculto(msg, !general);
      $("#form-voucher").classList.toggle("destacado", d.nivel === "media" && !voucherServidor);
      if (voucherServidor && !$("#voucher-hora").value && document.activeElement !== $("#voucher-hora")) {
        $("#voucher-hora").value = voucherServidor;
      }
      return;
    }

    mostrarPaso(3);
    texto($("#p3-ok"), d.voucher_hora ? `✓ Encontrado: pago de las ${d.voucher_hora} recibido` : "✓ Pago recibido");
    const listo = d.estado === "aceptado";
    const fallo = d.estado === "rechazado" || d.estado === "error_emision";
    oculto($("#p3-emitiendo"), listo || fallo);
    oculto($("#p3-listo"), !listo);
    oculto($("#p3-error"), !fallo);
    if (fallo) texto($("#p3-error-texto"), d.mensaje || "No se pudo emitir el comprobante.");
    if (!listo) {
      const m = $("#p3-mensaje");
      texto(m, d.mensaje || "");
      oculto(m, !d.mensaje);
      return;
    }
    $("#p3-segundos").textContent = d.segundos;
    $("#p3-numero").textContent = d.numero_formato ? `${d.tipo === "factura" ? "Factura" : "Boleta"} ${d.numero_formato}` : "";
    $("#lt-registro").textContent = d.registrado;
    $("#lt-pago").textContent = d.pago;
    $("#lt-pago-dif").textContent = d.seg_registro_pago != null ? `(+${Math.max(0, d.seg_registro_pago)} s)` : "";
    $("#lt-sunat").textContent = d.aceptado;
    $("#lt-sunat-dif").textContent = d.seg_pago_aceptado != null ? `(+${d.seg_pago_aceptado} s)` : "";
    $("#p3-destraba").textContent = d.rubro.que_destraba;
    if (d.pdf) {
      $("#btn-pdf").href = d.pdf;
      $("#btn-descargar").href = d.descargar;
      $("#qr-pdf").src = d.qr;
    }
    if (!animado) {
      animado = true;
      $(".celebra").classList.add("anima");
      if (!$("#i-nombre").value) $("#i-nombre").value = titulo(d.titular || "");
    }
  }

  function reloj() {
    if (!ultimo) return;
    const extra = Math.floor((performance.now() - recibido) / 1000);
    if (ultimo.estado === "esperando_pago") {
      texto($("#p2-reloj"), mmss(ultimo.esperando_seg + extra));
    } else if (ultimo.emitiendo_seg != null && ultimo.estado !== "aceptado") {
      texto($("#p3-reloj"), mmss(ultimo.emitiendo_seg + extra));
      const t = ultimo.estado === "pago_confirmado" || ultimo.estado === "emitiendo"
        ? "Emitiendo tu comprobante…" : "Esperando la respuesta de SUNAT…";
      $("#p3-estado").firstChild.textContent = t + " ";
    }
  }
  setInterval(reloj, 250);

  async function refrescar() {
    if (!demoId || consultando) return;
    consultando = true;
    try {
      const {status, datos} = await api(`/api/demos/${demoId}`);
      if (status === 404) {
        demoId = null;
        history.replaceState(null, "", location.pathname);
        mostrarPaso(1);
        return;
      }
      if (datos.ok) {
        ultimo = datos;
        recibido = performance.now();
        pintar(datos);
        reloj();
      }
    } catch (_) {
      const m = ultimo && ultimo.estado === "esperando_pago" ? $("#p2-mensaje") : $("#p3-mensaje");
      if (m) { m.textContent = "Tu conexión está inestable. Seguimos intentando…"; m.hidden = false; }
    } finally {
      consultando = false;
    }
  }

  function siguiente() {
    let espera = 2000;
    if (ultimo) {
      if (["aceptado", "rechazado", "error_emision", "expirado"].includes(ultimo.estado)) espera = 0;
      else if (ultimo.estado !== "esperando_pago") espera = 1000;
    }
    if (espera && demoId) refrescar().finally(() => setTimeout(siguiente, espera));
    else setTimeout(siguiente, 1000);
  }

  if (demoId) refrescar();
  else mostrarPaso(1);
  setTimeout(siguiente, 1000);
})();

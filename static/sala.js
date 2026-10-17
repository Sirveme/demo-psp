(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const clave = encodeURIComponent(window.SALA.clave);
  let vistos = new Set();
  let primera = true;

  const celda = (texto) => { const td = document.createElement("td"); td.textContent = texto; return td; };

  async function actualizar() {
    try {
      const r = await fetch(`/api/sala?k=${clave}`, {cache: "no-store"});
      if (!r.ok) return;
      const d = await r.json();
      $("#s-total").textContent = d.total;
      $("#s-promedio").textContent = d.promedio ?? "–";
      $("#s-record").textContent = d.record ?? "–";
      $("#s-curso").textContent = d.en_curso ? `${d.en_curso} demostración${d.en_curso > 1 ? "es" : ""} en curso…` : "";
      const tbody = $("#s-filas");
      if (!d.ultimas.length) return;
      tbody.replaceChildren(...d.ultimas.map((f) => {
        const tr = document.createElement("tr");
        const clave = `${f.hora}|${f.nombre}|${f.segundos}`;
        if (!primera && !vistos.has(clave)) tr.className = "nueva";
        vistos.add(clave);
        tr.append(celda(f.hora), celda(f.nombre), celda(f.rubro), celda(`S/ ${f.monto}`), celda(`${f.segundos} s`));
        return tr;
      }));
      primera = false;
    } catch (_) { /* reintenta en 3 s */ }
  }
  actualizar();
  setInterval(actualizar, 3000);
})();

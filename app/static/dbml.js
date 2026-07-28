/* Parser + visualizador de DBML, sin dependencias externas -- cubre el subset comun de
   la sintaxis (Table { columnas [pk/ref: > tabla.col] }, Ref: tabla.col > tabla2.col,
   comentarios //, Enum/TableGroup/Project se saltean sin romper el parseo). */
(function () {
  function parseDBML(texto) {
    const lineas = (texto || "").split("\n");
    const tablas = [];
    const refs = [];
    let i = 0;

    function sinComentario(l) {
      const idx = l.indexOf("//");
      return idx >= 0 ? l.slice(0, idx) : l;
    }

    while (i < lineas.length) {
      const linea = sinComentario(lineas[i]).trim();
      if (!linea) { i++; continue; }

      const mTabla = linea.match(/^Table\s+"?([\w.]+)"?(?:\s+as\s+\w+)?\s*\{/i);
      if (mTabla) {
        const tabla = { nombre: mTabla[1], columnas: [] };
        i++;
        while (i < lineas.length) {
          const interna = sinComentario(lineas[i]).trim();
          if (interna === "}") { i++; break; }
          if (!interna || /^(Note|indexes)\b/i.test(interna) || interna === "{") { i++; continue; }
          const mCol = interna.match(/^"?([\w]+)"?\s+([\w()\[\]]+)\s*(\[(.*)\])?/);
          if (mCol) {
            const col = { nombre: mCol[1], tipo: mCol[2] };
            if (mCol[4]) {
              if (/\bpk\b|primary key/i.test(mCol[4])) col.pk = true;
              const mRef = mCol[4].match(/ref:\s*([<>-])\s*"?([\w]+)"?\.([\w]+)/i);
              if (mRef) refs.push({ desde: { tabla: tabla.nombre, col: col.nombre }, hasta: { tabla: mRef[2], col: mRef[3] } });
            }
            tabla.columnas.push(col);
          }
          i++;
        }
        tablas.push(tabla);
        continue;
      }

      const mRefLinea = linea.match(/^Ref\s*:?\s*"?([\w]+)"?\.([\w]+)\s*[<>-]\s*"?([\w]+)"?\.([\w]+)/i);
      if (mRefLinea) {
        refs.push({ desde: { tabla: mRefLinea[1], col: mRefLinea[2] }, hasta: { tabla: mRefLinea[3], col: mRefLinea[4] } });
        i++;
        continue;
      }

      const mSaltear = linea.match(/^(Enum|TableGroup|Project)\s+.*\{/i);
      if (mSaltear) {
        i++;
        let profundidad = 1;
        while (i < lineas.length && profundidad > 0) {
          if (lineas[i].includes("{")) profundidad++;
          if (lineas[i].includes("}")) profundidad--;
          i++;
        }
        continue;
      }

      i++;
    }
    return { tablas, refs };
  }

  const ANCHO_TABLA = 220;
  const ALTO_HEADER = 30;
  const ALTO_COL = 24;
  const GAP_X = 70;
  const GAP_Y = 60;

  function calcularLayout(tablas) {
    const columnas = Math.max(1, Math.ceil(Math.sqrt(tablas.length)));
    const posiciones = {};
    const alturasFila = [];
    tablas.forEach((t, idx) => {
      const fila = Math.floor(idx / columnas);
      const alto = ALTO_HEADER + t.columnas.length * ALTO_COL;
      alturasFila[fila] = Math.max(alturasFila[fila] || 0, alto);
    });
    let y = 20;
    for (let fila = 0; fila < alturasFila.length; fila++) {
      for (let col = 0; col < columnas; col++) {
        const idx = fila * columnas + col;
        if (idx >= tablas.length) continue;
        posiciones[tablas[idx].nombre] = { x: 20 + col * (ANCHO_TABLA + GAP_X), y, alto: ALTO_HEADER + tablas[idx].columnas.length * ALTO_COL };
      }
      y += alturasFila[fila] + GAP_Y;
    }
    return { posiciones, anchoTotal: 20 + columnas * (ANCHO_TABLA + GAP_X), altoTotal: y };
  }

  function crearSvgPath(desde, hasta) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const midX = (desde.x + hasta.x) / 2;
    path.setAttribute("d", `M ${desde.x} ${desde.y} C ${midX} ${desde.y}, ${midX} ${hasta.y}, ${hasta.x} ${hasta.y}`);
    return path;
  }

  function initDbmlDiagram(contenedor, dbmlTexto) {
    const { tablas, refs } = parseDBML(dbmlTexto);
    if (!tablas.length) {
      contenedor.innerHTML = '<p class="muted" style="margin:0;">No se pudo interpretar ninguna tabla en este DBML.</p>';
      return;
    }
    const { posiciones, anchoTotal, altoTotal } = calcularLayout(tablas);

    contenedor.innerHTML = `
      <div class="dbml-toolbar">
        <button type="button" class="icon-btn" data-dbml-zoom-in title="Acercar">+</button>
        <button type="button" class="icon-btn" data-dbml-zoom-out title="Alejar">−</button>
        <button type="button" class="icon-btn" data-dbml-reset title="Restablecer vista">⟲</button>
      </div>
      <div class="dbml-viewport">
        <div class="dbml-canvas">
          <svg class="dbml-lines"></svg>
        </div>
      </div>
    `;
    const viewport = contenedor.querySelector(".dbml-viewport");
    const canvas = contenedor.querySelector(".dbml-canvas");
    const svg = contenedor.querySelector(".dbml-lines");
    canvas.style.width = anchoTotal + "px";
    canvas.style.height = altoTotal + "px";
    svg.setAttribute("width", anchoTotal);
    svg.setAttribute("height", altoTotal);

    const elementosTabla = {};
    tablas.forEach((t) => {
      const pos = posiciones[t.nombre];
      const div = document.createElement("div");
      div.className = "dbml-table";
      div.style.left = pos.x + "px";
      div.style.top = pos.y + "px";
      div.innerHTML =
        `<div class="dbml-table-header">${t.nombre}</div>` +
        t.columnas.map((c) => `<div class="dbml-col${c.pk ? " pk" : ""}"><span>${c.nombre}</span><span class="dbml-col-tipo">${c.tipo}</span></div>`).join("");
      canvas.appendChild(div);
      elementosTabla[t.nombre] = div;
    });

    const rutas = [];
    refs.forEach((r) => {
      const pDesde = posiciones[r.desde.tabla];
      const pHasta = posiciones[r.hasta.tabla];
      if (!pDesde || !pHasta) return;
      const idxDesde = (tablas.find((t) => t.nombre === r.desde.tabla) || { columnas: [] }).columnas.findIndex((c) => c.nombre === r.desde.col);
      const idxHasta = (tablas.find((t) => t.nombre === r.hasta.tabla) || { columnas: [] }).columnas.findIndex((c) => c.nombre === r.hasta.col);
      const yDesde = pDesde.y + ALTO_HEADER + Math.max(0, idxDesde) * ALTO_COL + ALTO_COL / 2;
      const yHasta = pHasta.y + ALTO_HEADER + Math.max(0, idxHasta) * ALTO_COL + ALTO_COL / 2;
      const desdeDerecha = pDesde.x < pHasta.x;
      const puntoDesde = { x: desdeDerecha ? pDesde.x + ANCHO_TABLA : pDesde.x, y: yDesde };
      const puntoHasta = { x: desdeDerecha ? pHasta.x : pHasta.x + ANCHO_TABLA, y: yHasta };
      const path = crearSvgPath(puntoDesde, puntoHasta);
      svg.appendChild(path);
      rutas.push({ path, desde: r.desde.tabla, hasta: r.hasta.tabla });
    });

    // Click en una tabla: resalta ella + sus relaciones directas, atenua el resto.
    let activa = null;
    function limpiarSeleccion() {
      activa = null;
      Object.values(elementosTabla).forEach((el) => el.classList.remove("dim", "activa"));
      rutas.forEach((r) => r.path.classList.remove("dim"));
    }
    Object.entries(elementosTabla).forEach(([nombre, el]) => {
      el.querySelector(".dbml-table-header").addEventListener("click", (ev) => {
        ev.stopPropagation();
        if (activa === nombre) { limpiarSeleccion(); return; }
        activa = nombre;
        const conectadas = new Set([nombre]);
        rutas.forEach((r) => {
          if (r.desde === nombre) conectadas.add(r.hasta);
          if (r.hasta === nombre) conectadas.add(r.desde);
        });
        Object.entries(elementosTabla).forEach(([n2, el2]) => {
          el2.classList.toggle("dim", !conectadas.has(n2));
          el2.classList.toggle("activa", n2 === nombre);
        });
        rutas.forEach((r) => r.path.classList.toggle("dim", r.desde !== nombre && r.hasta !== nombre));
      });
    });
    viewport.addEventListener("click", () => limpiarSeleccion());

    // Pan (arrastrar) + zoom (rueda / botones), con transform-origin fijo en 0,0.
    let escala = 1, tx = 0, ty = 0;
    function aplicarTransform() {
      canvas.style.transform = `translate(${tx}px, ${ty}px) scale(${escala})`;
    }
    let arrastrando = false, ultimoX = 0, ultimoY = 0;
    viewport.addEventListener("mousedown", (ev) => {
      arrastrando = true;
      ultimoX = ev.clientX; ultimoY = ev.clientY;
      viewport.classList.add("dragging");
    });
    window.addEventListener("mousemove", (ev) => {
      if (!arrastrando) return;
      tx += ev.clientX - ultimoX;
      ty += ev.clientY - ultimoY;
      ultimoX = ev.clientX; ultimoY = ev.clientY;
      aplicarTransform();
    });
    window.addEventListener("mouseup", () => { arrastrando = false; viewport.classList.remove("dragging"); });

    function zoom(factor, cx, cy) {
      const nuevaEscala = Math.min(2, Math.max(0.3, escala * factor));
      tx = cx - (cx - tx) * (nuevaEscala / escala);
      ty = cy - (cy - ty) * (nuevaEscala / escala);
      escala = nuevaEscala;
      aplicarTransform();
    }
    viewport.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      const rect = viewport.getBoundingClientRect();
      zoom(ev.deltaY < 0 ? 1.1 : 0.9, ev.clientX - rect.left, ev.clientY - rect.top);
    }, { passive: false });

    contenedor.querySelector("[data-dbml-zoom-in]").addEventListener("click", () => zoom(1.2, viewport.clientWidth / 2, viewport.clientHeight / 2));
    contenedor.querySelector("[data-dbml-zoom-out]").addEventListener("click", () => zoom(0.8, viewport.clientWidth / 2, viewport.clientHeight / 2));
    contenedor.querySelector("[data-dbml-reset]").addEventListener("click", () => { escala = 1; tx = 0; ty = 0; aplicarTransform(); });
  }

  window.initDbmlDiagram = initDbmlDiagram;
})();

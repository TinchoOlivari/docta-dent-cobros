/* JS de Docta Dent (016). Sin build. API por atributos heredada de 015; ver contracts/componentes.md.
 * El navegador solo avisa: el servidor decide siempre (principio II). */
(function () {
  "use strict";
  var MAX_BYTES = 5242880;

  // Formato visual de monto: opera sobre texto de dígitos, nunca convierte a número (principio III).
  // Punto con 1-2 dígitos detrás = decimal; con 3 = miles. Solo al pegar o al salir del campo (015/FR-004).
  function formatearMonto(texto, completar, pegado) {
    var neg = /^\s*-/.test(texto) ? "-" : "";
    var s = texto.replace(/[^0-9.,]/g, "");
    var ic = s.indexOf(","), ip = s.lastIndexOf(".");
    var ent = s, dec = "", coma = false;
    if (ic >= 0 && ip > ic) { ent = s.slice(0, ip); dec = s.slice(ip + 1); coma = true; }
    else if (ic >= 0) { ent = s.slice(0, ic); dec = s.slice(ic + 1); coma = true; }
    else if ((completar || pegado) && ip >= 0 && s.length - ip - 1 >= 1 && s.length - ip - 1 <= 2) {
      ent = s.slice(0, ip); dec = s.slice(ip + 1); coma = true;
    }
    ent = ent.replace(/\D/g, "").replace(/^0+(?=\d)/, "");
    dec = dec.replace(/\D/g, "").slice(0, 2);
    if (ent === "" && dec === "" && !coma) return neg;
    ent = (ent || "0").replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    if (completar) return neg + ent + "," + (dec + "00").slice(0, 2);
    return neg + (coma ? ent + "," + dec : ent);
  }
  function sinPuntos(texto) { return texto.replace(/\./g, ""); }
  window.formatearMonto = formatearMonto;

  // Valida el texto de un monto (020/FR-001). Sin parseFloat: cadena de dígitos a entero de centavos.
  var E_FORMATO = "Escribí los centavos con coma, por ejemplo 1.500,50.";
  var E_CERO = "El monto debe ser mayor que $0,00.";
  var E_DIGITOS = "El monto puede tener hasta 10 dígitos antes de la coma.";
  var E_DECIMALES = "Escribí hasta 2 decimales."; // mismo texto que el servidor (FR-030)
  // Cero es válido solo si el llamador lo permite (tope semanal 0 = sin límite; mínimo $0,00).
  function validarMonto(texto, permiteCero) {
    var t = texto.trim();
    var neg = t.charAt(0) === "-";
    if (neg) t = t.slice(1);
    var m = /^(\d{1,3}(?:\.\d{3})*|\d+)(?:,(\d*))?$/.exec(t);
    if (!m) return { error: E_FORMATO };
    var dec = m[2] || "";
    if (dec.length > 2) return { error: E_DECIMALES };
    if (/,$/.test(t)) return { error: E_FORMATO };
    var ent = m[1].replace(/\./g, "");
    if (neg) return { error: E_CERO };
    if (ent.replace(/^0+/, "").length > 10) return { error: E_DIGITOS };
    var centavos = Number(ent + (dec + "00").slice(0, 2));
    return centavos > 0 || permiteCero ? { centavos: centavos } : { error: E_CERO };
  }
  window.validarMonto = validarMonto;

  // `corto`: texto del tooltip de la tabla de prestaciones (024/FR-005); el lector de pantalla lee `texto`.
  function errorMonto(entrada, texto, corto) {
    var caja = entrada.closest("[data-monto]") || entrada.parentNode;
    var p = caja.querySelector("[data-monto-error]");
    if (!texto) {
      if (p) p.remove();
      entrada.removeAttribute("aria-invalid");
      entrada.removeAttribute("aria-describedby");
      return;
    }
    if (!p) {
      p = document.createElement("p");
      p.className = "err-msg";
      p.setAttribute("data-monto-error", "");
      p.id = "monto-error-" + (entrada.id || entrada.name || "campo");
      caja.appendChild(p);
    }
    p.textContent = texto;
    p.dataset.texto = corto || texto;
    entrada.setAttribute("aria-invalid", "true");
    entrada.setAttribute("aria-describedby", p.id);
  }

  window.errorMonto = errorMonto;

  // Centavos del campo o null. Marca el error bajo el campo; un campo vacío no es error aquí (required decide).
  window.montoCentavos = function (entrada, permiteCero) {
    if (!entrada.value.trim()) { errorMonto(entrada, ""); return null; }
    var r = validarMonto(entrada.value, permiteCero);
    errorMonto(entrada, r.error || "");
    return r.error ? null : r.centavos;
  };

  // Cada función de inicio se marca en el elemento para no repetirse cuando HTMX trae HTML nuevo.
  function una(el, marca) {
    if (el.dataset[marca]) return false;
    el.dataset[marca] = "1";
    return true;
  }

  function montos(raiz) {
    raiz.querySelectorAll("[data-monto] input").forEach(function (entrada) {
      if (entrada.type !== "text" || !una(entrada, "montoListo")) return;
      if (entrada.value) entrada.value = formatearMonto(entrada.value, true);
      // El evento input no toca el texto (020/FR-001). Al salir: formatea solo un texto válido.
      entrada.addEventListener("blur", function () {
        if (window.montoCentavos(entrada, true) !== null) entrada.value = formatearMonto(entrada.value, true);
      });
    });
  }

  function mostrarEnvio(form, enviando) {
    form.querySelectorAll("button[type=submit]").forEach(function (b) {
      b.disabled = enviando;
      var s = b.querySelector("[data-enviando]");
      if (s) s.classList.toggle("hidden", !enviando);
    });
  }

  function bloquearEnvio(form) {
    if (!una(form, "envioListo")) return;
    form.addEventListener("submit", function (e) {
      if (form.dataset.enviado) { e.preventDefault(); return; }
      var malo = null;
      form.querySelectorAll("[data-monto] input").forEach(function (i) {
        if (i.type !== "text" || !i.value.trim()) return;
        var r = validarMonto(i.value, true);
        errorMonto(i, r.error || "");
        if (r.error) { malo = malo || i; return; }
        i.value = sinPuntos(formatearMonto(i.value, true));
      });
      if (malo) { e.preventDefault(); malo.focus(); return; }
      if (form.hasAttribute("data-descarga")) return;
      form.dataset.enviado = "1";
      // El botón se deshabilita después del submit para que su name/value viaje.
      setTimeout(function () { mostrarEnvio(form, true); }, 0);
    });
    // Volver con el botón "atrás" restaura la página desde caché: dejar enviar de nuevo.
    window.addEventListener("pageshow", function (e) {
      if (!e.persisted) return;
      delete form.dataset.enviado;
      mostrarEnvio(form, false);
    });
  }

  function etiquetaTamano(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1048576) return (bytes / 1024).toFixed(0) + " KB";
    return (bytes / 1048576).toFixed(1).replace(".", ",") + " MB";
  }

  function validarArchivo(archivo, libre) {
    if (archivo.size === 0) return "El archivo está vacío.";
    if (archivo.size > MAX_BYTES) return "El archivo pesa más de 5 MB.";
    if (!libre && !/^image\//.test(archivo.type) && archivo.type !== "application/pdf") return "Solo se aceptan imágenes o PDF.";
    return "";
  }

  // pdf.js se pide la 1.ª vez que se elige un PDF y se reusa la promesa (017/R2).
  var promesaPdfjs = null;
  function cargarPdfjs() {
    if (!promesaPdfjs) {
      promesaPdfjs = import(document.body.dataset.pdfjs).then(function (m) {
        m.GlobalWorkerOptions.workerSrc = document.body.dataset.pdfjsWorker;
        return m;
      });
      promesaPdfjs.catch(function () { promesaPdfjs = null; });
    }
    return promesaPdfjs;
  }
  function iconoArchivo() {
    var t = document.getElementById("icono-archivo");
    return t ? t.content.cloneNode(true) : document.createDocumentFragment();
  }
  function miniaturaPdf(archivo) {
    var tarea = null;
    var trabajo = Promise.all([cargarPdfjs(), archivo.arrayBuffer()]).then(function (r) {
      tarea = r[0].getDocument({ data: r[1], isEvalSupported: false });
      return tarea.promise;
    }).then(function (d) {
      return d.getPage(1);
    }).then(function (pagina) {
      var base = pagina.getViewport({ scale: 1 });
      var vista = pagina.getViewport({ scale: 160 / base.width });
      var canvas = document.createElement("canvas");
      canvas.className = "miniatura";
      canvas.width = Math.floor(vista.width);
      canvas.height = Math.floor(vista.height);
      canvas.setAttribute("role", "img");
      canvas.setAttribute("aria-label", "Vista previa de la página 1 de " + archivo.name);
      return pagina.render({ canvasContext: canvas.getContext("2d"), viewport: vista }).promise.then(function () { return canvas; });
    });
    var limite = new Promise(function (_, rechazar) { setTimeout(function () { rechazar(new Error("tiempo")); }, 10000); });
    return Promise.race([trabajo, limite]).finally(function () { if (tarea) tarea.destroy(); });
  }

  function zonaComprobante(zona) {
    var entrada = zona.querySelector("input[type=file]");
    if (!entrada || !una(zona, "zonaLista")) return;
    var varios = zona.hasAttribute("data-multiple");
    var libre = zona.hasAttribute("data-libre"); // certificado y clave de ARCA (022/FR-016)
    if (!libre) entrada.setAttribute("accept", "image/*,application/pdf");
    if (varios) entrada.multiple = true;
    var lista = zona.querySelector("[data-elegidos]");
    var aviso = zona.querySelector("[data-error]");
    if (!aviso) {
      aviso = document.createElement("p");
      aviso.className = "err-msg";
      aviso.setAttribute("role", "alert");
      zona.appendChild(aviso);
    }
    var elegidos = [];
    var urls = [];
    function liberar() { urls.forEach(function (u) { URL.revokeObjectURL(u); }); urls = []; }
    window.addEventListener("pagehide", liberar);

    // 024/FR-010: un clic en la vista previa abre el archivo en grande en otra pestaña.
    function enlace(a, contenido) {
      var u = URL.createObjectURL(a);
      urls.push(u);
      var link = document.createElement("a");
      link.href = u;
      link.target = "_blank";
      link.rel = "noopener";
      link.className = "flex-none";
      link.setAttribute("aria-label", "Ver " + a.name + " en grande");
      link.appendChild(contenido);
      return link;
    }
    function miniatura(a, li) {
      if (libre) return;
      if (/^image\//.test(a.type)) {
        var img = document.createElement("img");
        img.className = "miniatura";
        img.alt = "Vista previa de " + a.name;
        img.onerror = function () { img.replaceWith(iconoArchivo()); };
        var link = enlace(a, img);
        img.src = link.href;
        li.prepend(link);
      } else {
        miniaturaPdf(a).then(function (canvas) {
          if (li.isConnected) li.prepend(enlace(a, canvas));
        }).catch(function () {
          if (li.isConnected) li.prepend(iconoArchivo());
        });
      }
    }

    function pintar() {
      var dt = new DataTransfer();
      elegidos.forEach(function (a) { dt.items.add(a); });
      entrada.files = dt.files;
      if (!lista) return;
      liberar();
      lista.textContent = "";
      // 024/FR-010: con un solo archivo, la zona se vuelve una tarjeta con la vista previa grande.
      zona.classList.toggle("con-archivo", !varios && !libre && elegidos.length > 0);
      elegidos.forEach(function (a, i) {
        var li = document.createElement("li");
        li.className = "file";
        var nombre = document.createElement("span");
        nombre.className = "min-w-0 flex-1 truncate";
        nombre.textContent = a.name;
        nombre.title = a.name;
        var peso = document.createElement("span");
        peso.className = "tabular-nums text-ink-3";
        peso.textContent = etiquetaTamano(a.size);
        var quitar = document.createElement("button");
        quitar.type = "button";
        quitar.className = "btn btn-ghost";
        quitar.textContent = "Quitar";
        quitar.addEventListener("click", function () { elegidos.splice(i, 1); pintar(); });
        li.append(nombre, peso, quitar);
        lista.appendChild(li);
        miniatura(a, li);
      });
    }
    function error(texto) {
      aviso.textContent = texto;
      aviso.hidden = !texto;
    }
    function recibir(archivos) {
      var nuevos = Array.prototype.slice.call(archivos);
      if (!nuevos.length) return;
      error("");
      if (!varios) nuevos = nuevos.slice(0, 1);
      for (var i = 0; i < nuevos.length; i++) {
        var e = validarArchivo(nuevos[i], libre);
        if (e) {
          error(e);
          if (!varios) elegidos = [];
          pintar(); // el input vuelve a lo ya elegido: un archivo inválido nunca viaja
          return;
        }
      }
      elegidos = varios ? elegidos.concat(nuevos) : nuevos;
      pintar();
    }
    entrada.addEventListener("change", function () { recibir(entrada.files); });
    var drop = zona.querySelector(".drop") || zona;
    function resalte(on) { drop.classList.toggle("on", on); }
    zona.addEventListener("dragenter", function () { resalte(true); });
    zona.addEventListener("dragover", function (e) { e.preventDefault(); resalte(true); });
    zona.addEventListener("dragleave", function (e) { if (!zona.contains(e.relatedTarget)) resalte(false); });
    zona.addEventListener("drop", function (e) {
      e.preventDefault();
      resalte(false);
      if (e.dataTransfer && e.dataTransfer.files.length) recibir(e.dataTransfer.files);
    });
    document.addEventListener("paste", function (e) {
      var fs = e.clipboardData && e.clipboardData.files;
      if (fs && fs.length) recibir(fs);
    });
  }

  function copiar(boton) {
    if (!una(boton, "copiarListo")) return;
    boton.addEventListener("click", function () {
      var original = boton.textContent;
      function aviso(t) { boton.textContent = t; setTimeout(function () { boton.textContent = original; }, 2000); }
      var valor = boton.dataset.copiar;
      if (!navigator.clipboard) { aviso("No se pudo copiar"); return; }
      navigator.clipboard.writeText(valor).then(
        function () { aviso("Copiado"); },
        function () { aviso("No se pudo copiar"); }
      );
    });
  }

  function normalizar(t) { return t.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase(); }
  function filtrarLista(entrada) {
    var cont = document.querySelector(entrada.dataset.filtrar);
    if (!cont || !una(entrada, "filtroListo")) return;
    entrada.addEventListener("input", function () {
      var q = normalizar(entrada.value), visibles = 0;
      Array.prototype.forEach.call(cont.children, function (h) {
        if (h.hasAttribute("data-vacio")) return;
        var ok = normalizar(h.textContent).indexOf(q) >= 0;
        h.hidden = !ok;
        if (ok) visibles++;
      });
      var vacio = cont.querySelector("[data-vacio]");
      if (!vacio) {
        vacio = document.createElement("p");
        vacio.setAttribute("data-vacio", "");
        vacio.className = "p-3 text-ink-3";
        vacio.textContent = "Sin resultados";
        cont.appendChild(vacio);
      }
      vacio.hidden = visibles > 0;
    });
  }

  // Un botón con `formaction` puede dejar cambiada la `action` del formulario: vuelve a la original.
  function restaurarAcciones() {
    document.querySelectorAll("form[data-action-original]").forEach(function (f) {
      f.setAttribute("action", f.dataset.actionOriginal);
    });
  }
  window.addEventListener("pageshow", function () {
    restaurarAcciones();
    document.querySelectorAll("dialog [data-confirmar]").forEach(function (b) { b.disabled = false; });
  });

  // Diálogo de confirmación. Esc lo cierra sin enviar; el foco vuelve al botón que lo abrió (015/FR-013).
  window.abrirConfirmar = function (id, alConfirmar) {
    var d = document.getElementById(id);
    if (!d) return;
    var origen = document.activeElement;
    var ok = d.querySelector("[data-confirmar]"), no = d.querySelector("[data-cerrar]");
    ok.disabled = false;
    // Un 2.º clic no hace nada: el botón queda deshabilitado desde el 1.º (018/FR-032).
    ok.onclick = function () {
      if (ok.disabled) return;
      ok.disabled = true;
      d.close();
      if (alConfirmar) alConfirmar();
    };
    no.onclick = function () { d.close(); };
    d.onclick = function (e) { if (e.target === d) no.onclick(); }; // clic en el fondo (022/FR-036)
    d.addEventListener("close", function volver() {
      d.removeEventListener("close", volver);
      restaurarAcciones();
      if (origen && origen.focus && document.contains(origen)) origen.focus();
    });
    d.showModal();
    if (no) no.focus();
  };

  function programarCierre(aviso) {
    if (!aviso.hasAttribute("data-autocerrar") || aviso.dataset.programado) return;
    aviso.dataset.programado = "1";
    setTimeout(function () { aviso.remove(); }, 6000);
  }

  function mostrarErrorHtmx() {
    var plantilla = document.getElementById("aviso-error-htmx");
    var zona = document.querySelector("[data-avisos]");
    if (!plantilla || !zona) return;
    zona.appendChild(plantilla.content.cloneNode(true));
  }

  function avisos() {
    // Los avisos llegan en el HTML; se reinsertan para que aria-live los anuncie.
    var zona = document.querySelector("[data-avisos]");
    if (!zona) return;
    var lista = Array.prototype.slice.call(zona.children);
    lista.forEach(function (a) { a.remove(); });
    setTimeout(function () {
      lista.forEach(function (a) { zona.appendChild(a); programarCierre(a); });
    }, 100);
  }

  function autofoco() {
    var raiz = document.getElementById("contenido");
    if (!raiz) return;
    // [data-foco-pc]: foco al cargar solo en pantallas de 1024 px o más (021/FR-004).
    var pc = raiz.querySelector("[data-foco-pc]");
    if (pc && window.matchMedia("(min-width: 1024px)").matches && !raiz.querySelector("[aria-invalid=true]")) {
      pc.focus();
      return;
    }
    var sel = "input:not([type=hidden]):not([type=file]):not([disabled]):not([data-sin-foco]), select:not([disabled]), textarea:not([disabled])";
    var objetivo = raiz.querySelector(
      "[aria-invalid=true]:not([type=hidden]):not([disabled])"
    );
    if (!objetivo) {
      // Sin autofoco en el celular (abre el teclado) ni en buscadores y filtros de listas.
      if (window.matchMedia("(pointer: coarse)").matches) return;
      var form = raiz.querySelector("form");
      if (form && !form.hasAttribute("data-sin-foco") && !form.matches("[role=search]")) {
        objetivo = form.querySelector(sel);
        if (objetivo && objetivo.type === "search") objetivo = null;
      }
    }
    if (objetivo) objetivo.focus();
  }

  function iniciar(raiz) {
    // El navegador no muestra su globo: los errores salen en línea (018/FR-004).
    if (raiz.matches && raiz.matches("form")) raiz.noValidate = true;
    raiz.querySelectorAll("form").forEach(function (f) { f.noValidate = true; });
    montos(raiz);
    raiz.querySelectorAll("form[method=post]").forEach(bloquearEnvio);
    raiz.querySelectorAll("[data-comprobante]").forEach(zonaComprobante);
    raiz.querySelectorAll("[data-copiar]").forEach(copiar);
    raiz.querySelectorAll("[data-filtrar]").forEach(filtrarLista);
    raiz.querySelectorAll("input[type=date]").forEach(calendario);
    raiz.querySelectorAll("input[type=month]").forEach(mes);
    combos(raiz);  // después de mes(): toma sus 2 select (023/FR-004)
  }

  // Delegación: sirve también para avisos y diálogos que aparecen después.
  document.addEventListener("click", function (e) {
    var cerrar = e.target.closest("[data-cerrar-aviso]");
    if (cerrar) {
      var aviso = cerrar.closest("[role=alert], [role=status]") || cerrar.parentElement;
      aviso.remove();
      return;
    }
    var b = e.target.closest("[data-confirmar-dialogo]");
    if (b) {
      // Es un botón de envío: sin JS el formulario viaja y el servidor valida (FR-009).
      e.preventDefault();
      var f = document.getElementById(b.dataset.confirmarForm) || b.form;
      if (!f || !validarEnLinea(f)) return;
      window.abrirConfirmar(b.dataset.confirmarDialogo, function () {
        if (b.hasAttribute("data-enviar-directo")) f.submit(); else f.requestSubmit();
      });
    }
  });

  // Errores bajo el campo, con ícono, en español, en lugar del globo nativo (FR-004, FR-016, FR-028).
  var CLASE_ERROR = { INPUT: "input-error", SELECT: "select-error", TEXTAREA: "textarea-error" };
  var contadorIds = 0;
  function mensajeDe(c) {
    if (c.validity.valueMissing) return c.tagName === "SELECT" || c.type === "radio" ? "Elegí una opción." : "Completá este campo.";
    return c.dataset.mensaje || "Revisá este campo.";
  }
  function quitarDeDescribedby(c, id) {
    var d = (c.getAttribute("aria-describedby") || "").split(" ").filter(function (x) { return x && x !== id; });
    if (d.length) c.setAttribute("aria-describedby", d.join(" ")); else c.removeAttribute("aria-describedby");
  }
  function limpiarError(c) {
    var p = document.getElementById(c.id + "-error-js");
    if (p) p.remove();
    c.removeAttribute("aria-invalid");
    var clase = CLASE_ERROR[c.tagName];
    if (clase && c.dataset.errorJs) c.classList.remove(clase);
    delete c.dataset.errorJs;
    quitarDeDescribedby(c, c.id + "-error-js");
  }
  function iconoError() {
    var t = document.getElementById("icono-error");
    return t ? t.content.cloneNode(true) : document.createDocumentFragment();
  }
  function validarEnLinea(form) {
    var primero = null, grupos = {};
    Array.prototype.forEach.call(form.elements, function (c) {
      if (!c.willValidate || c.type === "hidden" || c.type === "submit" || c.type === "button") return;
      if (!c.id) c.id = "campo-js-" + (++contadorIds);
      limpiarError(c);
      if (c.checkValidity() || !c.checkVisibility()) return;
      if (c.type === "radio") { if (grupos[c.name]) return; grupos[c.name] = true; }  // 1 error por grupo
      var viejo = document.getElementById(c.id + "-error");  // error del servidor del mismo campo
      if (viejo) { viejo.remove(); quitarDeDescribedby(c, viejo.id); }
      var p = document.createElement("p");
      p.id = c.id + "-error-js";
      p.className = "err-msg";
      p.setAttribute("role", "alert");
      var texto = document.createElement("span");
      texto.textContent = mensajeDe(c);
      p.append(iconoError(), texto);
      var cont = c.closest(".field") || c.closest("fieldset");
      if (cont) cont.appendChild(p); else c.insertAdjacentElement("afterend", p);
      c.setAttribute("aria-invalid", "true");
      c.setAttribute("aria-describedby", ((c.getAttribute("aria-describedby") || "") + " " + p.id).trim());
      var clase = CLASE_ERROR[c.tagName];
      if (clase && !c.classList.contains(clase)) { c.classList.add(clase); c.dataset.errorJs = "1"; }
      c.addEventListener("input", function quitar() { c.removeEventListener("input", quitar); limpiarError(c); });
      c.addEventListener("change", function quitar() { c.removeEventListener("change", quitar); limpiarError(c); });
      if (!primero) primero = c;
    });
    if (primero) primero.focus();
    return !primero;
  }
  // Captura: corre antes que htmx y que cualquier otro submit. Con error frena el envío.
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (!f.matches || !f.matches("form") || (e.submitter && e.submitter.hasAttribute("formnovalidate"))) return;
    if (!validarEnLinea(f)) { e.preventDefault(); e.stopPropagation(); }
  }, true);

  // --- Ayudas (i), menús de "Exportar" y calendario: `details` de daisyUI (FR-006, FR-021, FR-028) ---
  var DETALLES = "details[data-ayuda], details[data-menu]";
  function cerrarDetalle(d, devolverFoco) {
    d.open = false;
    var s = d.querySelector("summary");
    if (devolverFoco && s) s.focus();
  }
  document.addEventListener("toggle", function (e) {
    var d = e.target;
    if (!d.matches || !d.matches(DETALLES)) return;
    var s = d.querySelector(":scope > summary");
    if (s && d.open && s.getAttribute("aria-disabled") === "true") { d.open = false; return; }  // menú deshabilitado (023/FR-007)
    if (s) s.setAttribute("aria-expanded", d.open ? "true" : "false");
    if (d.open) {
      document.querySelectorAll(DETALLES).forEach(function (o) { if (o !== d && o.open && !o.contains(d) && !d.contains(o)) o.open = false; });
    }
  }, true);
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    var abiertos = document.querySelectorAll("details[open]");
    for (var i = abiertos.length - 1; i >= 0; i--) {
      if (abiertos[i].matches(DETALLES)) { cerrarDetalle(abiertos[i], true); e.stopPropagation(); return; }
    }
  }, true);
  document.addEventListener("click", function (e) {
    var ds = e.target.closest && e.target.closest('summary[aria-disabled="true"]');
    if (ds) { e.preventDefault(); return; }
    // Elegir una opción cierra el menú (023/FR-007).
    var op = e.target.closest && e.target.closest(".menu-acciones a, .menu-acciones button");
    if (op) { var m = op.closest("details[data-menu]"); if (m) m.open = false; }
    document.querySelectorAll(DETALLES).forEach(function (d) {
      if (d.open && !d.contains(e.target)) d.open = false;
    });
  });
  // Un panel `fixed` sin `top` cae en la posición que tendría con la página sin scroll, o sea fuera de
  // la pantalla si la página está desplazada (018 fix). Se ubica bajo el ícono, o encima si no entra.
  function ubicarPanel(d) {
    var panel = d.querySelector(":scope > .ayuda-panel");
    var s = d.querySelector(":scope > summary");
    if (!panel || !s || getComputedStyle(panel).position !== "fixed") return;
    var r = s.getBoundingClientRect();
    panel.style.top = Math.round(r.bottom + 4) + "px";
    if (window.innerWidth >= 640) {
      var ancho = panel.offsetWidth || 288;
      panel.style.left = Math.round(Math.max(8, Math.min(r.left, window.innerWidth - ancho - 8))) + "px";
    }
    requestAnimationFrame(function () {
      var alto = panel.offsetHeight;
      if (alto && r.bottom + 4 + alto > window.innerHeight && r.top - 4 - alto >= 0) {
        panel.style.top = Math.round(r.top - 4 - alto) + "px";
      }
    });
  }
  document.addEventListener("pointerover", function (e) {
    var d = e.target.closest && e.target.closest(DETALLES);
    if (d) ubicarPanel(d);
  });
  document.addEventListener("toggle", function (e) {
    if (e.target.matches && e.target.matches(DETALLES) && e.target.open) ubicarPanel(e.target);
  }, true);
  // Un panel `fixed` abierto no sigue a su ícono al desplazar: se ubica de nuevo.
  document.addEventListener("scroll", function () {
    document.querySelectorAll(DETALLES).forEach(function (d) { if (d.open) ubicarPanel(d); });
  }, true);

  // --- Combobox con búsqueda dentro de la lista: Tom Select (build popular, plugin dropdown_input) se pide
  // la 1.ª vez que hay un select sin [multiple] ni [data-nativo] (018/FR-019, 023/FR-004) ---
  var promesaTomSelect = null;
  function cargarTomSelect() {
    if (!promesaTomSelect) {
      promesaTomSelect = new Promise(function (ok, no) {
        var s = document.createElement("script");
        s.src = document.body.dataset.tomselect;
        s.onload = function () { ok(window.TomSelect); };
        s.onerror = function () { promesaTomSelect = null; no(new Error("tom-select")); };
        document.head.appendChild(s);
      });
    }
    return promesaTomSelect;
  }
  function combos(raiz) {
    var lista = raiz.querySelectorAll("select:not([multiple]):not([data-nativo]):not(.tomselected)");
    if (!lista.length) return;
    cargarTomSelect().then(function (TomSelect) {
      lista.forEach(function (sel) {
        if (!una(sel, "comboListo")) return;
        sel.classList.remove("select", "select-error");  // el contenedor de Tom Select copia las clases del select
        new TomSelect(sel, {
          plugins: ["dropdown_input"],
          create: false,
          maxOptions: null,
          allowEmptyOption: true,
          render: { no_results: function () { return '<div class="no-results">Sin resultados</div>'; } },
        });
      });
    }).catch(function () { /* sin Tom Select queda el select nativo */ });
  }

  // El JS que cambia .value u .options de un select debe llamar esto después (023/R1).
  window.sincronizarSelect = function (sel) { if (sel && sel.tomselect) sel.tomselect.sync(); };

  // --- Calendario: Cally se pide la 1.ª vez que hay un input[type=date] (FR-027) ---
  var promesaCally = null;
  function cargarCally() {
    if (!promesaCally) {
      promesaCally = import(document.body.dataset.cally);
      promesaCally.catch(function () { promesaCally = null; });
    }
    return promesaCally;
  }
  var FLECHA = { anterior: "M15.75 19.5 8.25 12l7.5-7.5", siguiente: "m8.25 4.5 7.5 7.5-7.5 7.5" };
  function flecha(slot, etiqueta, trazo) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", "size-4 fill-current");
    svg.setAttribute("aria-label", etiqueta);
    svg.setAttribute("slot", slot);
    var path = document.createElementNS(ns, "path");
    path.setAttribute("d", trazo);
    svg.appendChild(path);
    return svg;
  }
  var E_FECHA = "Escribí la fecha como dd/mm/aaaa.";
  // Cally habla ISO (aaaa-mm-dd); el campo muestra dd/mm/aaaa. Ambos formatos son válidos al escribir.
  function aIso(texto) {
    var t = texto.trim(), m;
    if ((m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(t))) t = m[3] + "-" + m[2] + "-" + m[1];
    if (!/^\d{4}-\d{2}-\d{2}$/.test(t)) return "";
    var d = new Date(t + "T00:00:00Z");
    return !isNaN(d) && d.toISOString().slice(0, 10) === t ? t : "";
  }
  function aVisible(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
    return m ? m[3] + "/" + m[2] + "/" + m[1] : iso;
  }
  var MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
  // input[type=month] -> 2 select (mes y año) y un hidden con el mismo name y valor aaaa-mm (021/FR-056).
  function mes(entrada) {
    if (entrada.type !== "month" || !una(entrada, "mesListo")) return;
    var hoy = new Date().getFullYear();
    var min = parseInt((entrada.min || "").slice(0, 4), 10) || hoy - 10;
    var max = parseInt((entrada.max || "").slice(0, 4), 10) || hoy + 1;
    var m = /^(\d{4})-(\d{2})$/.exec(entrada.value);
    var anio = m ? parseInt(m[1], 10) : hoy;
    var mesActual = m ? parseInt(m[2], 10) : 0;
    var oculto = document.createElement("input");
    oculto.type = "hidden";
    oculto.name = entrada.name;
    oculto.value = entrada.value;
    var selMes = document.createElement("select");
    var selAnio = document.createElement("select");
    selMes.className = selAnio.className = "select w-auto";  // sin ancho fijo: mide lo que pide su texto (023/FR-021)
    selMes.setAttribute("aria-label", "Mes");
    selAnio.setAttribute("aria-label", "Año");
    if (!mesActual) selMes.add(new Option("Mes", ""));
    MESES.forEach(function (n, i) { selMes.add(new Option(n.charAt(0).toUpperCase() + n.slice(1), String(i + 1), false, i + 1 === mesActual)); });
    for (var a = Math.min(min, anio); a <= Math.max(max, anio); a++) selAnio.add(new Option(String(a), String(a), false, a === anio));
    function sincronizar() {
      var nm = selMes.value;
      oculto.value = nm ? selAnio.value + "-" + (nm.length < 2 ? "0" + nm : nm) : "";
      oculto.dispatchEvent(new Event("change", { bubbles: true }));
    }
    selMes.addEventListener("change", sincronizar);
    selAnio.addEventListener("change", sincronizar);
    var fila = document.createElement("div");
    fila.className = "flex items-start gap-2";
    if (entrada.id) selMes.id = entrada.id;
    fila.append(selMes, selAnio, oculto);
    entrada.replaceWith(fila);
  }
  function calendario(entrada) {
    if (entrada.type !== "date" || entrada.dataset.fechaLista) return;
    cargarCally().then(function () {
      if (!una(entrada, "fechaLista")) return;
      entrada.type = "text";
      entrada.placeholder = "dd/mm/aaaa";
      entrada.pattern = "\\d{2}/\\d{2}/\\d{4}|\\d{4}-\\d{2}-\\d{2}";
      entrada.autocomplete = "off";
      entrada.value = aVisible(entrada.value);
      entrada.inputMode = "numeric";
      // Solo dígitos (máximo 8) y barras automáticas; un pegado aaaa-mm-dd o d/m/aaaa pasa por aIso() (023/FR-005).
      entrada.addEventListener("input", function (e) {
        var v = entrada.value, d;
        if (e.inputType === "insertFromPaste" && aIso(v)) v = aVisible(aIso(v));
        else if (e.inputType === "insertFromPaste" && (d = /^\s*(\d{1,2})\/(\d{1,2})\/(\d{4})\s*$/.exec(v)))
          v = ("0" + d[1]).slice(-2) + ("0" + d[2]).slice(-2) + d[3];
        d = v.replace(/\D/g, "").slice(0, 8);
        var salida = d.slice(0, 2);
        if (d.length > 2) salida += "/" + d.slice(2, 4);
        if (d.length > 4) salida += "/" + d.slice(4);
        if (salida !== entrada.value) entrada.value = salida;
      });
      var fila = document.createElement("div");
      fila.className = "flex items-start gap-2";
      entrada.replaceWith(fila);
      var menu = document.createElement("details");
      menu.className = "dropdown dropdown-end";
      menu.setAttribute("data-menu", "");
      var boton = document.createElement("summary");
      boton.className = "btn btn-secundario btn-sm-icono";
      boton.setAttribute("aria-label", "Elegir fecha");
      boton.setAttribute("aria-expanded", "false");
      var icono = document.getElementById("icono-calendario");
      if (icono) boton.appendChild(icono.content.cloneNode(true));
      var panel = document.createElement("div");
      panel.className = "dropdown-content cal-panel";
      var cal = document.createElement("calendar-date");
      cal.className = "cally";
      cal.setAttribute("locale", "es-AR");
      cal.setAttribute("first-day-of-week", "1");
      cal.append(flecha("previous", "Mes anterior", FLECHA.anterior), flecha("next", "Mes siguiente", FLECHA.siguiente), document.createElement("calendar-month"));
      panel.appendChild(cal);
      menu.append(boton, panel);
      fila.append(entrada, menu);
      menu.addEventListener("toggle", function () {
        var iso = aIso(entrada.value);
        if (menu.open && iso) cal.value = iso;
      });
      // Texto inválido al salir del campo: error bajo el campo (021/FR-056).
      entrada.addEventListener("blur", function () {
        var malo = entrada.value.trim() !== "" && !aIso(entrada.value);
        var caja = fila.parentNode;
        var p = caja.querySelector("[data-fecha-error]");
        if (!malo) {
          if (p) p.remove();
          entrada.removeAttribute("aria-invalid");
          entrada.removeAttribute("aria-describedby");
          return;
        }
        if (!p) {
          p = document.createElement("p");
          p.className = "err-msg";
          p.setAttribute("data-fecha-error", "");
          p.id = "fecha-error-" + (entrada.id || entrada.name || "campo");
          fila.after(p);
        }
        p.textContent = E_FECHA;
        entrada.setAttribute("aria-invalid", "true");
        entrada.setAttribute("aria-describedby", p.id);
      });
      cal.addEventListener("change", function () {
        entrada.value = aVisible(cal.value);
        entrada.dispatchEvent(new Event("input", { bubbles: true }));
        entrada.dispatchEvent(new Event("change", { bubbles: true }));
        cerrarDetalle(menu, true);
      });
    }).catch(function () { /* sin Cally queda el campo de fecha nativo */ });
  }

  document.addEventListener("DOMContentLoaded", function () {
    iniciar(document);
    avisos();
    autofoco();
  });
  document.addEventListener("htmx:afterSettle", function (e) { iniciar(e.target); });
  // Filtros GET: no mandar campos vacíos (la URL queda con solo lo elegido).
  document.addEventListener("htmx:configRequest", function (e) {
    if (e.detail.verb !== "get") return;
    var p = e.detail.parameters;
    Object.keys(p).forEach(function (k) { if (p[k] === "") delete p[k]; });
  });
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (!f.matches || !f.matches("form[method=get]") || f.hasAttribute("hx-get")) return;
    var vacios = Array.prototype.filter.call(f.elements, function (c) {
      return c.name && c.value === "" && !c.disabled && c.type !== "submit";
    });
    vacios.forEach(function (c) { c.disabled = true; });
    setTimeout(function () { vacios.forEach(function (c) { c.disabled = false; }); }, 0);
  });
  // Avisos que llegan por hx-swap-oob a [data-avisos]: se cierran a los 6 s (018/FR-031).
  document.addEventListener("htmx:oobAfterSwap", function (e) {
    var t = e.detail && e.detail.target;
    if (t && t.matches && t.matches("[data-avisos]")) t.querySelectorAll("[data-autocerrar]").forEach(programarCierre);
  });
  document.addEventListener("htmx:responseError", mostrarErrorHtmx);
  document.addEventListener("htmx:sendError", mostrarErrorHtmx);

  document.addEventListener("alpine:init", function () {
    var Alpine = window.Alpine;

    // Menú lateral: contraído (escritorio) y cajón (celular). La cookie la lee el servidor (research P6).
    Alpine.data("shell", function () {
      return {
        collapsed: /(?:^|;\s*)dd_menu=1/.test(document.cookie),
        drawer: false,
        alternar: function () {
          this.collapsed = !this.collapsed;
          document.cookie = "dd_menu=" + (this.collapsed ? "1" : "0") + "; path=/sistema/; max-age=31536000; SameSite=Lax";
        },
        abrir: function () {
          this.drawer = true;
          document.documentElement.style.overflow = "hidden";  // la página de atrás no se desplaza (021/FR-059)
          this.$nextTick(function () {
            var primero = Array.prototype.find.call(
              document.querySelectorAll("#menu .nav a"),
              function (el) { return el.offsetParent !== null; }
            );
            if (primero) primero.focus();
          });
        },
        // Foco atrapado en el menú móvil abierto: Tab y Mayús+Tab dan la vuelta (FR-013).
        trampa: function (e) {
          if (!this.drawer || e.key !== "Tab") return;
          var f = Array.prototype.filter.call(
            document.querySelectorAll("#menu a, #menu button"),
            function (el) { return el.offsetParent !== null; }
          );
          if (!f.length) return;
          var primero = f[0], ultimo = f[f.length - 1];
          if (e.shiftKey && document.activeElement === primero) { e.preventDefault(); ultimo.focus(); }
          else if (!e.shiftKey && document.activeElement === ultimo) { e.preventDefault(); primero.focus(); }
        },
        cerrar: function () {
          if (!this.drawer) return;
          this.drawer = false;
          document.documentElement.style.overflow = "";
          var boton = this.$refs.abrir;
          if (boton) boton.focus();
        },
      };
    });

    // Buscador de pacientes: "Buscar" se habilita con 1 carácter o más, sin contar espacios (018/FR-009).
    // `q` arranca con el valor que trajo el servidor, antes de que x-model lo pise.
    Alpine.data("buscador", function () {
      return {
        q: "",
        init: function () {
          var campo = this.$root.querySelector("#q");
          this.q = campo ? campo.value : "";
        },
        get vacio() { return this.q.trim() === ""; },
      };
    });

    // Expone `metodo` (valor del campo) para x-show / x-text. Sin JS, todo queda visible.
    Alpine.data("segunMetodo", function (selector) {
      return {
        metodo: "",
        init: function () {
          var self = this;
          function leer() {
            var campos = document.querySelectorAll(selector);
            for (var i = 0; i < campos.length; i++) {
              var c = campos[i];
              if (c.type === "radio") { if (c.checked) return c.value; }
              else return c.value;
            }
            return "";
          }
          self.metodo = leer();
          document.querySelectorAll(selector).forEach(function (c) {
            c.addEventListener("change", function () { self.metodo = leer(); });
          });
        },
      };
    });

    // Cuenta las casillas name="pk" elegidas (015/FR-021 de la lista de cobros).
    Alpine.data("seleccion", function () {
      return {
        n: 0,
        hay: 0,  // casillas visibles
        todos: "ninguno",  // "ninguno" | "algunos" | "todos" sobre las casillas visibles (023/FR-003)
        casillas: function () {
          return Array.prototype.filter.call(
            this.$root.querySelectorAll('input[name="pk"], input[name="pks"]'),
            function (c) { return c.checkVisibility(); }
          );
        },
        init: function () {
          var self = this;
          function contar() {
            self.n = self.$root.querySelectorAll('input[name="pk"]:checked, input[name="pks"]:checked').length;
            var v = self.casillas();
            var marcadas = v.filter(function (c) { return c.checked; }).length;
            self.hay = v.length;
            self.todos = !marcadas ? "ninguno" : marcadas === v.length ? "todos" : "algunos";
          }
          self.$root.addEventListener("change", contar);
          document.addEventListener("htmx:afterSettle", contar);
          contar();
        },
        // Como Gmail: vacía -> todas; intermedia o marcada -> ninguna.
        elegirTodos: function () {
          var marcar = this.todos === "ninguno";
          this.casillas().forEach(function (c) {
            if (c.checked !== marcar) {
              c.checked = marcar;
              c.dispatchEvent(new Event("change", { bubbles: true }));
            }
          });
        },
      };
    });
  });
})();

// Arrastrar filas de la lista de prioridad (017/FR-023). Envía el formulario "Mover a" de la fila.
(function () {
  "use strict";
  var tbody = document.querySelector("tbody[data-ordenable]");
  if (!tbody || !window.Sortable) return;
  tbody.querySelectorAll("[data-asa]").forEach(function (a) {
    a.hidden = false;
    a.classList.add("inline-flex");
  });
  window.Sortable.create(tbody, {
    handle: "[data-asa]",
    animation: 150,
    ghostClass: "sortable-ghost",
    onEnd: function (e) {
      if (e.newIndex === e.oldIndex) return;
      var form = e.item.querySelector("form[data-mover]");
      form.querySelector("input[name=posicion]").value = e.newIndex + 1;
      form.requestSubmit();
    },
  });
})();

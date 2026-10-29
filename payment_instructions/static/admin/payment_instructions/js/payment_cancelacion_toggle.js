/* Muestra la sección "Cancelación" del admin de Payment solo cuando el Estado
 * elegido es "Cancelado". Sigue existiendo en el DOM siempre: es la única forma
 * de cancelar a mano un pago ya cobrado (ver cancelar_pagos en admin.py), así
 * que no se puede sacar del formulario, solo ocultar visualmente. */
(function () {
    "use strict";

    function togglear(estadoSelect, secciones) {
        var mostrar = estadoSelect.value === "CANCELADO";
        secciones.forEach(function (seccion) {
            seccion.style.display = mostrar ? "" : "none";
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        var estadoSelect = document.getElementById("id_estado");
        var secciones = document.querySelectorAll(".cancelacion-fieldset");
        if (!estadoSelect || !secciones.length) return;

        togglear(estadoSelect, secciones);
        estadoSelect.addEventListener("change", function () {
            togglear(estadoSelect, secciones);
        });
    });
})();

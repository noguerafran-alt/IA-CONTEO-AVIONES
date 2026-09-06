/* La franja de "desde donde se esta midiendo", igual en las cinco paginas.
 *
 * POR QUE EXISTE. El 23/08 la antena se mudo a Aeroparque y el servidor se
 * arranco sin ADSB_RECEIVER: midio nueve horas desde San Isidro, a 13,3 km de
 * la pista, mientras las operaciones detectadas estaban a 0,19-1,15 km. La
 * configuracion vive SOLO en el entorno del proceso -nada del repo la escribe
 * en un archivo- y de las cinco paginas ninguna la mostraba donde se leen los
 * numeros: /adsb/mapa era la unica que nombraba al receptor, y encima al revés
 * -pintaba el cartel solo si is_default era false, o sea que avisaba cuando
 * alguien habia elegido la ubicacion a proposito y se callaba exactamente
 * cuando nadie la eligio, que es el modo de falla-.
 *
 * UN SOLO ARCHIVO Y UN SOLO ENDPOINT (/api/receptor). Con cinco copias del
 * cartel, cuatro se desincronizan: ya paso con el dict de receptor de
 * /api/aeropuerto/mapa, que traia lat/lon/name y nada mas.
 *
 * Y la version POR DEFECTO es la ruidosa. El caso tranquilizador es el otro:
 * si alguien puso ADSB_RECEIVER, eligio; si no lo puso, nadie eligio nada y
 * todo lo que la pagina diga de la antena puede estar hablando de otro lugar.
 */
(function () {
  var CSS = `
  .franja-rx { border-radius: 8px; padding: 11px 15px; margin: 0 0 18px;
               font-size: 0.87rem; line-height: 1.55; }
  .franja-rx b { color: #e0f2fe; }
  .franja-rx.elegida { background: #12202c; border-left: 3px solid #38bdf8; color: #bae6fd; }
  .franja-rx.defecto { background: #3a1b1b; border-left: 3px solid #f87171; color: #fecaca; }
  .franja-rx.defecto b { color: #fee2e2; }
  .franja-rx .rx-detalle { color: inherit; opacity: 0.85; display: block; margin-top: 3px; }
  .franja-rx code { font-size: 0.85em; }
  .franja-rx.up { margin-top: -12px; }
  .franja-rx.tibia { background: #2b230f; border-left: 3px solid #facc15; color: #fde68a; }
  .franja-rx.tibia b { color: #fef9c3; }
  `;

  function estilo() {
    if (document.getElementById("css-franja-rx")) return;
    var s = document.createElement("style");
    s.id = "css-franja-rx";
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  function km(v) { return v === null || v === undefined ? "–" : v.toFixed(1) + " km"; }

  // El texto se arma con los datos que manda el servidor, nunca con un lugar
  // escrito en el HTML: este repo ya tuvo tres frases con "San Isidro"
  // hardcodeado que habrian afirmado San Isidro con la antena en Aeroparque.
  function html(rx, proceso) {
    var obj = rx.objetivo;
    var pista = "";
    if (obj) {
      pista = ` La pista de <b>${obj.codigo}</b> queda a <b>${km(obj.km)}</b>: `
            + (obj.surface_visible
               ? "según esta configuración, un avión <em>en la pista</em> entra en el horizonte de radio."
               : "según esta configuración, un avión <em>en la pista</em> queda BAJO el horizonte de radio y no se oiría.");
    }
    var comun = `antena <b>${rx.antenna_m} m</b>, horizonte de radio a un avión en el suelo `
              + `<b>${km(rx.surface_horizon_km)}</b>.` + pista;
    var proc = proceso
      ? `<span class="rx-detalle">Contesta el proceso PID ${proceso.pid} `
        + `(<code>${proceso.ejecutable}</code>).</span>` : "";

    if (rx.is_default) {
      return `<div class="franja-rx defecto">
        <b>Nadie configuró dónde está la antena.</b> Se está midiendo todo desde
        <b>${rx.name}</b> (${rx.lat.toFixed(4)}, ${rx.lon.toFixed(4)}), que es el valor
        <b>por defecto</b>: ${comun}
        <span class="rx-detalle">Si la antena se movió, todo lo que esta página diga de
        distancias, alcance y horizonte está referido al lugar equivocado. Se arregla
        arrancando con <code>ADSB_RECEIVER</code> puesto (o con
        <code>MEDIR-EN-AEROPARQUE.bat</code>).</span>${proc}</div>`;
    }
    return `<div class="franja-rx elegida">
      Midiendo desde <b>${rx.name}</b> (${rx.lat.toFixed(4)}, ${rx.lon.toFixed(4)}),
      elegido con <code>ADSB_RECEIVER</code>: ${comun}${proc}</div>`;
  }

  /* ---- la banda de uptime ------------------------------------------------
   *
   * POR QUE VA ACA Y NO EN CADA PAGINA. Es la misma decision que la franja del
   * receptor, por el mismo motivo: son cinco paginas y cuatro copias se
   * desincronizan. Se pinta SIEMPRE que se pinte la franja, asi que ninguna
   * plantilla tuvo que cambiar y ninguna se puede olvidar de mostrarlo.
   *
   * Y LA VERSION RUIDOSA ES LA QUE AVISA CUANDO HUBO CAIDAS, igual que la
   * franja avisa cuando nadie eligio receptor. El caso tranquilizador es
   * "estuvo arriba todo el tiempo"; cualquier otro tiene que interrumpir, porque
   * un numero de esta pagina calculado sobre una ventana en la que el grabador
   * estuvo caido no es un numero, es una fraccion de uno sin decir de que.
   *
   * SIN REGISTRO NO SE DIBUJA UN CERO. cobertura null -o uptime null- significa
   * "no se puede afirmar", y eso se escribe con palabras. Un 0% ahi seria una
   * afirmacion sobre la antena que nadie midio. Ver adsb_uptime.resumen().
   */
  // 100 % SOLO si de verdad no falto un segundo. Con un redondeo a un decimal,
  // una cobertura de 99,95% con un hueco de 12 s se dibujaba "100,0 %", que es
  // afirmar que no hubo hueco cuando el mismo cartel dice al lado que si lo
  // hubo. Se corta hacia abajo, que es el lado honesto.
  function pct(v) {
    var p = v * 100;
    if (v < 1 && p > 99.9) return "99,9 %";
    return p.toFixed(1).replace(".", ",") + " %";
  }

  function duracion(s) {
    if (s === null || s === undefined) return "–";
    if (s < 90) return Math.round(s) + " s";
    if (s < 5400) return Math.round(s / 60) + " min";
    return (s / 3600).toFixed(1).replace(".", ",") + " h";
  }

  function htmlUptime(up) {
    if (!up) {
      return `<div class="franja-rx defecto up"><b>No hay registro de grabación
        para estas 24 h.</b> Puede ser una base anterior al registro, o que no se
        haya podido leer.
        <span class="rx-detalle">Sin este dato no se puede saber si un hueco en los
        datos es que no voló nadie o que no estábamos escuchando, así que
        <b>ningún porcentaje de cobertura de esta página se puede sostener</b>.</span></div>`;
    }
    if (up.cobertura === null || up.cobertura === undefined) {
      return `<div class="franja-rx defecto up"><b>El registro de grabación no cubre
        estas 24 h.</b> Hay ${up.sesiones} sesión(es) anotadas, pero no alcanzan para
        calcular cobertura.
        <span class="rx-detalle">Los porcentajes que dependan de cuánto estuvimos
        escuchando no se pueden publicar todavía.</span></div>`;
    }

    var avisos = [];
    if (up.caidas > 0) {
      avisos.push(`el grabador se <b>cayó ${up.caidas} vez(ces)</b> sin cerrar
        (corte de luz, cuelgue o <code>taskkill</code>)`);
    }
    if (up.solapamientos > 0) {
      // No deberia pasar -- el dongle es exclusivo de un proceso -- y si pasa,
      // el tiempo arriba se calculo uniendo intervalos. Se dice, no se tapa.
      avisos.push(`hay <b>${up.solapamientos} sesión(es) solapadas</b>, que no
        deberían existir con un solo dongle`);
    }

    var comun = `El grabador estuvo arriba el <b>${pct(up.cobertura)}</b> de las
      últimas 24 h (${up.sesiones} sesión(es)${up.corriendo ? ", corriendo ahora" : ""}).
      El hueco más largo sin grabar fue de <b>${duracion(up.hueco_max_s)}</b>.`;

    if (avisos.length) {
      return `<div class="franja-rx defecto up">${comun}
        <span class="rx-detalle">Además, ${avisos.join("; ")}.
        Un porcentaje calculado sobre esta ventana tiene que publicarse
        <b>con esta cobertura al lado</b>.</span></div>`;
    }
    // Todavia no es "tranquilizador" si falto tiempo: un 78% sin caidas sigue
    // siendo una ventana con agujeros, y el share de esa ventana los hereda.
    if (up.cobertura < 0.99) {
      return `<div class="franja-rx tibia up">${comun}
        <span class="rx-detalle">No hubo caídas, pero la ventana <b>no está
        completa</b>: cualquier conteo de este período habla del tiempo grabado,
        no del día.</span></div>`;
    }
    return `<div class="franja-rx elegida up">${comun}</div>`;
  }

  function cajaUptime(id) {
    var caja = document.getElementById(id || "franja-receptor");
    if (!caja) return null;
    var sub = caja.nextElementSibling;
    if (!sub || sub.className !== "caja-uptime") {
      sub = document.createElement("div");
      sub.className = "caja-uptime";
      caja.parentNode.insertBefore(sub, caja.nextSibling);
    }
    return sub;
  }

  window.pintarFranjaUptime = function (uptime, id) {
    var sub = cajaUptime(id);
    if (!sub) return;
    estilo();
    sub.innerHTML = htmlUptime(uptime);
  };

  window.cargarFranjaUptime = async function (id) {
    var sub = cajaUptime(id);
    if (!sub) return;
    try {
      var d = await (await fetch("/api/adsb/uptime")).json();
      window.pintarFranjaUptime(d.uptime, id);
    } catch (e) {
      // Falla APARTE de la franja del receptor: que no se pueda leer el
      // registro no puede borrar el cartel de desde donde se mide.
      window.pintarFranjaUptime(null, id);
    }
  };

  // Se le puede pasar el receptor ya cargado (las paginas que igual piden un
  // endpoint que lo trae no hacen un segundo pedido) o dejar que lo busque.
  // El uptime SIEMPRE se pide aca: es barato -una tabla de una fila por sesion-
  // y asi ninguna de las cinco paginas puede quedarse sin mostrarlo.
  window.pintarFranjaReceptor = function (rx, proceso, id) {
    var caja = document.getElementById(id || "franja-receptor");
    if (!caja || !rx) return;
    estilo();
    caja.innerHTML = html(rx, proceso);
    window.cargarFranjaUptime(id);
  };

  window.cargarFranjaReceptor = async function (id) {
    var caja = document.getElementById(id || "franja-receptor");
    if (!caja) return;
    try {
      var d = await (await fetch("/api/receptor")).json();
      window.pintarFranjaReceptor(d.receptor, d.proceso, id);
    } catch (e) {
      // Ni una franja vacia ni un lugar inventado: si no se pudo saber desde
      // donde se mide, eso tambien es informacion.
      estilo();
      caja.innerHTML = `<div class="franja-rx defecto"><b>No se pudo saber desde dónde se
        está midiendo</b> (${e}). Los números de distancia y alcance de esta página no se
        pueden interpretar sin ese dato.</div>`;
      // El uptime igual: son dos preguntas distintas y que falle una no puede
      // dejar a la otra sin contestar.
      window.cargarFranjaUptime(id);
    }
  };
})();

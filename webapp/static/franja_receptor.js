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

  // Se le puede pasar el receptor ya cargado (las paginas que igual piden un
  // endpoint que lo trae no hacen un segundo pedido) o dejar que lo busque.
  window.pintarFranjaReceptor = function (rx, proceso, id) {
    var caja = document.getElementById(id || "franja-receptor");
    if (!caja || !rx) return;
    estilo();
    caja.innerHTML = html(rx, proceso);
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
    }
  };
})();

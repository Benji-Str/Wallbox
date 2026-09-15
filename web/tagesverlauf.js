/* Tagesverlauf und Farben — von der Hauptoberflaeche UND vom Wanddisplay
   benutzt. Deshalb eine eigene Datei: derselbe Graph soll nicht an zwei
   Stellen gepflegt werden.

   Die Darstellung folgt dem openWB-Display: oberhalb der Nulllinie das, was
   in den Haushalt hineinfliesst (PV gruen, Netzbezug rot darauf), unterhalb
   gespiegelt der Verbrauch (Haus grau, Auto blau), gestrichelt der
   Speicher-Ladestand auf eigener Prozentachse.                            */
const SVGNS = 'http://www.w3.org/2000/svg';
const F = {pv:'#3ddc97', bezug:'#ff6b6b', einspeisung:'#a8e6c0',
           haus:'#8496b0', laden:'#5aa9ff', soc:'#ffcf5c'};
let STAG = '';           // angezeigter Tag

const kw = w => (w / 1000);

function pfad(punkte){
  return punkte.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
}

/* Zusammenhängende Abschnitte ohne Lücken. Über eine Lücke hinweg zu malen
   würde Messwerte erfinden, die es nicht gibt. */
function abschnitte(n, gueltig){
  const out = []; let a = null;
  for (let i = 0; i < n; i++){
    if (gueltig(i)){ if (a === null) a = i; }
    else { if (a !== null && i - a > 1) out.push([a, i - 1]); a = null; }
  }
  if (a !== null && n - a > 1) out.push([a, n - 1]);
  return out;
}

/* `hoehe` gibt die Zeichenhoehe im Koordinatensystem vor. Das Wanddisplay
   hat einen anderen Zuschnitt als die Statistik-Seite; ohne den Parameter
   bliebe dort die halbe Flaeche leer, weil der Graph sein Seitenverhaeltnis
   behaelt. */
function tagesKurve(d, hoehe){
  const n = d.n || 0;
  if (!n){
    return `<div class="mut" style="padding:26px 0">Für diesen Tag liegen keine
      Messwerte vor. Aufgezeichnet wird ab dem Zeitpunkt, an dem die Steuerung
      läuft.</div>`;
  }
  const B = 960, H = Math.max(200, Math.min(900, hoehe || 330)),
        L = 52, R = 52, O = 12, U = 26;
  const nl = O + (H - O - U) * 0.56;              // Nulllinie
  const obenH = nl - O, untenH = H - U - nl;
  const g = a => (a || []).map(v => v === null || v === undefined ? null : v);
  const pv = g(d.pv), netz = g(d.netz), haus = g(d.haus), lad = g(d.laden), soc = g(d.soc);
  const bezug = netz.map(v => v === null ? null : Math.max(0, v));
  const x = i => L + (B - L - R) * (n === 1 ? .5 : i / (n - 1));

  // gemeinsamer Maßstab für oben und unten — zwei Maßstäbe würden die
  // Verhältnisse verzerren
  let max = 500;
  for (let i = 0; i < n; i++){
    max = Math.max(max, (pv[i] || 0) + (bezug[i] || 0), (haus[i] || 0) + (lad[i] || 0));
  }
  const yo = v => nl - obenH * Math.min(1, v / max);
  const yu = v => nl + untenH * Math.min(1, v / max);

  function band(werte, basis, yfn, farbe, deckung){
    const ok = i => werte[i] !== null;
    return abschnitte(n, ok).map(([a, b]) => {
      const oben = [], unten = [];
      for (let i = a; i <= b; i++){
        oben.push([x(i), yfn((basis[i] || 0) + werte[i])]);
        unten.push([x(i), yfn(basis[i] || 0)]);
      }
      return `<path d="${pfad(oben)} ${pfad(unten.reverse()).replace('M', 'L')} Z"
        fill="${farbe}" fill-opacity="${deckung}" stroke="${farbe}"
        stroke-width="1" stroke-opacity=".9"/>`;
    }).join('');
  }

  const null0 = new Array(n).fill(0);
  let svg = '';
  // waagerechte Hilfslinien alle 1 kW
  const schritt = max > 12000 ? 5000 : max > 6000 ? 2000 : 1000;
  for (let v = schritt; v <= max; v += schritt){
    for (const [yy, vz] of [[yo(v), ''], [yu(v), '-']]){
      svg += `<line x1="${L}" x2="${B - R}" y1="${yy}" y2="${yy}"
        stroke="#223148" stroke-width="1"/>
        <text x="${L - 7}" y="${yy + 4}" fill="#8496b0" font-size="11"
          text-anchor="end">${vz}${kw(v).toFixed(v >= 1000 ? 1 : 2)}</text>`;
    }
  }
  svg += band(pv, null0, yo, F.pv, .5);
  svg += band(bezug, pv.map(v => v || 0), yo, F.bezug, .5);
  svg += band(haus, null0, yu, F.haus, .45);
  svg += band(lad, haus.map(v => v || 0), yu, F.laden, .5);

  // Ladestand auf eigener Achse rechts (0–100 %), wie bei openWB
  const sok = i => soc[i] !== null;
  svg += abschnitte(n, sok).map(([a, b]) => {
    const p = [];
    for (let i = a; i <= b; i++) p.push([x(i), O + (H - O - U) * (1 - soc[i] / 100)]);
    return `<path d="${pfad(p)}" fill="none" stroke="${F.soc}" stroke-width="2"
      stroke-dasharray="5 4"/>`;
  }).join('');
  for (const pz of [0, 50, 100]){
    const yy = O + (H - O - U) * (1 - pz / 100);
    svg += `<text x="${B - R + 7}" y="${yy + 4}" fill="#8496b0"
      font-size="11">${pz} %</text>`;
  }

  svg += `<line x1="${L}" x2="${B - R}" y1="${nl}" y2="${nl}"
    stroke="#43536e" stroke-width="1.5"/>`;
  // Stundenraster. Bei 288 Stützstellen fallen mehrere Punkte in dieselbe
  // Stunde — ohne diese Sperre stehen die Beschriftungen übereinander.
  let letzteStunde = -1;
  for (let i = 0; i < n; i++){
    const dt = new Date(d.t[i] * 1000);
    if (dt.getMinutes() > 4 || dt.getHours() % 3) continue;
    if (dt.getHours() === letzteStunde) continue;
    letzteStunde = dt.getHours();
    svg += `<line x1="${x(i)}" x2="${x(i)}" y1="${O}" y2="${H - U}"
      stroke="#1b2740" stroke-width="1"/>
      <text x="${x(i)}" y="${H - 8}" fill="#8496b0" font-size="11"
        text-anchor="middle">${String(dt.getHours()).padStart(2, '0')}:00</text>`;
  }
  const legende = [['PV', F.pv], ['Netzbezug', F.bezug], ['Haus', F.haus],
                   ['Auto', F.laden], ['Ladestand', F.soc]]
    .map(([t, f]) => `<span class="row" style="gap:6px"><i style="width:12px;height:12px;
      border-radius:3px;background:${f};display:inline-block"></i>${t}</span>`).join('');
  return `<div style="overflow-x:auto"><svg viewBox="0 0 ${B} ${H}"
      style="width:100%;min-width:620px;display:block">${svg}</svg></div>
    <div class="row" style="gap:16px;margin-top:10px;font-size:12px;color:var(--mut)">
      ${legende}</div>`;
}

/* Tagesenergie als unterteilte Säulen — die waagerechten Trennlinien sind
   dieselbe Ablesehilfe wie am openWB-Display: eine Stufe = eine feste
   Anzahl kWh. */

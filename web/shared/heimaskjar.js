/* heimaskjar – shared helpers for all pages: sun and sky colour, Icelandic dates and
   numbers, and the family-member filter. Plain script, no build step; defines globals. */

const HS = (() => {
  const LAT = 64.15, LON = -21.94;                 // Reykjavík, for the sun

  const DAYS = ["sunnudagur", "mánudagur", "þriðjudagur", "miðvikudagur",
                "fimmtudagur", "föstudagur", "laugardagur"];
  const MONTHS = ["janúar", "febrúar", "mars", "apríl", "maí", "júní", "júlí",
                  "ágúst", "september", "október", "nóvember", "desember"];

  /* sun height (degrees) at Reykjavík; good to a fraction of a degree */
  function sunElevation(d) {
    const rad = Math.PI / 180;
    const n = (Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) -
               Date.UTC(d.getUTCFullYear(), 0, 0)) / 864e5;
    const g = 2 * Math.PI / 365 * (n - 1 + (d.getUTCHours() - 12) / 24);
    const eot = 229.18 * (0.000075 + 0.001868 * Math.cos(g) - 0.032077 * Math.sin(g)
                - 0.014615 * Math.cos(2 * g) - 0.040849 * Math.sin(2 * g));        // minutes
    const decl = 0.006918 - 0.399912 * Math.cos(g) + 0.070257 * Math.sin(g)
               - 0.006758 * Math.cos(2 * g) + 0.000907 * Math.sin(2 * g)
               - 0.002697 * Math.cos(3 * g) + 0.00148 * Math.sin(3 * g);           // radians
    const minutes = d.getUTCHours() * 60 + d.getUTCMinutes() + eot + 4 * LON;
    const ha = (minutes / 4 - 180) * rad;
    const lat = LAT * rad;
    return Math.asin(Math.sin(lat) * Math.sin(decl) +
                     Math.cos(lat) * Math.cos(decl) * Math.cos(ha)) / rad;
  }

  /* sky colour for a sun height: night → twilight → sunrise glow → day */
  const SKY = [[-18, "#10162a"], [-10, "#1b2340"], [-5, "#3a3a64"], [-2, "#7a5272"],
               [0, "#c98a64"], [2, "#dcb995"], [5, "#d9dee2"], [12, "#e6edf3"], [30, "#f1f6fa"]];
  const hex = c => [1, 3, 5].map(i => parseInt(c.slice(i, i + 2), 16));
  function skyColour(el) {
    if (el <= SKY[0][0]) return SKY[0][1];
    for (let i = 1; i < SKY.length; i++) {
      if (el <= SKY[i][0]) {
        const [e0, c0] = SKY[i - 1], [e1, c1] = SKY[i], t = (el - e0) / (e1 - e0);
        const a = hex(c0), b = hex(c1);
        return "rgb(" + a.map((v, k) => Math.round(v + (b[k] - v) * t)).join(",") + ")";
      }
    }
    return SKY[SKY.length - 1][1];
  }

  /* colour the page for the sun at time d; fills an element with id "sun" if there is one */
  function applySky(d) {
    const el = sunElevation(d), dark = el < -1;
    const s = document.documentElement.style;
    s.setProperty("--bg", skyColour(el));
    s.setProperty("--fg", dark ? "#eef2f7" : "#1d2530");
    s.setProperty("--muted", dark ? "#b7c0cc" : "#5b6673");
    s.setProperty("--card", dark ? "rgba(255,255,255,.08)" : "rgba(255,255,255,.7)");
    const sun = document.getElementById("sun");
    if (sun) sun.textContent =
      el > 0 ? `sól ${el.toFixed(0)}° yfir sjóndeildarhring` :
      el > -6 ? "ljósaskipti" : el > -12 ? "rökkur" : "myrkur";
    return el;
  }

  /* Iceland is UTC all year, so UTC fields are local time */
  const pad = n => String(n).padStart(2, "0");
  const hhmm = d => pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes());
  const dayName = d => `${DAYS[d.getUTCDay()]} ${d.getUTCDate()}. ${MONTHS[d.getUTCMonth()]}`;
  const sameDay = (a, b) => a.toISOString().slice(0, 10) === b.toISOString().slice(0, 10);
  /* whole days from today to d: 0 today, 1 tomorrow, -1 yesterday */
  const daysFromToday = (d, now = new Date()) =>
    Math.round((Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) -
                Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())) / 864e5);
  /* "í dag", "á morgun" or the date */
  const whichDay = d => ({ 0: "í dag", 1: "á morgun" })[daysFromToday(d)] ?? dayName(d);
  /* number with an Icelandic decimal comma */
  const num = (v, digits = 0) => v == null ? "–" :
    Number(v).toFixed(digits).replace(".", ",").replace(/^-0(,0+)?$/, "0$1");

  /* ---- family-member filter: ?person=id in the address, remembered per browser ---- */
  function person() {
    const p = new URLSearchParams(location.search).get("person");
    if (p !== null) { try { localStorage.setItem("heimaskjar.person", p); } catch (e) {} return p; }
    try { return localStorage.getItem("heimaskjar.person") || ""; } catch (e) { return ""; }
  }
  /* a link that keeps the chosen person */
  function link(href) {
    const p = person();
    if (!p) return href;
    return href + (href.includes("?") ? "&" : "?") + "person=" + encodeURIComponent(p);
  }

  async function json(url) {
    const r = await fetch(url + (url.includes("?") ? "&" : "?") + "t=" + Date.now());
    if (!r.ok) throw new Error(r.status + " " + url);
    return r.json();
  }

  return { LAT, LON, DAYS, MONTHS, sunElevation, skyColour, applySky,
           pad, hhmm, dayName, sameDay, daysFromToday, whichDay, num, person, link, json };
})();

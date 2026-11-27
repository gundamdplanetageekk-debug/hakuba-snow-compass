/* Hakuba Snow Compass — scoring engine (no DOM). Works in the browser (window.Engine) and Node (module.exports). */
(function (root) {
  'use strict';

  const HOURLY_VARS = [
    'temperature_2m', 'snowfall', 'precipitation', 'weather_code',
    'wind_speed_10m', 'wind_gusts_10m', 'wind_direction_10m',
    'visibility', 'freezing_level_height',
    'wind_speed_850hPa', 'wind_direction_850hPa', 'temperature_850hPa',
    'wind_speed_700hPa', 'wind_direction_700hPa', 'temperature_700hPa'
  ];

  /** One request for every resort: [top, base] per resort, elevation-corrected. */
  function buildOpenMeteoUrl(resorts, opts) {
    opts = opts || {};
    const lat = [], lon = [], ele = [];
    resorts.forEach(r => {
      lat.push(r.coords.top[0], r.coords.base[0]);
      lon.push(r.coords.top[1], r.coords.base[1]);
      ele.push(r.top, r.base);
    });
    const host = opts.apiKey ? 'https://customer-api.open-meteo.com' : 'https://api.open-meteo.com';
    const q = new URLSearchParams({
      latitude: lat.join(','), longitude: lon.join(','), elevation: ele.join(','),
      hourly: HOURLY_VARS.join(','), timezone: 'Asia/Tokyo',
      past_days: '3', forecast_days: '3', wind_speed_unit: 'kmh'
    });
    if (opts.apiKey) q.set('apikey', opts.apiKey);
    return host + '/v1/forecast?' + q.toString();
  }

  /** Current JST hour as "YYYY-MM-DDTHH:00" (matches Open-Meteo local time strings). */
  function jstHourString(date) {
    const d = new Date((date ? date.getTime() : Date.now()) + 9 * 3600e3);
    return d.toISOString().slice(0, 13) + ':00';
  }

  function normalize(raw, resorts) {
    const list = Array.isArray(raw) ? raw : [raw];
    if (list.length < resorts.length * 2) throw new Error('Weather payload has ' + list.length + ' locations, expected ' + resorts.length * 2);
    const out = {};
    resorts.forEach((r, i) => { out[r.id] = { top: list[i * 2].hourly, base: list[i * 2 + 1].hourly }; });
    return out;
  }

  const num = v => (typeof v === 'number' && isFinite(v) ? v : null);
  const sum = a => a.reduce((s, v) => s + (num(v) || 0), 0);
  const max = a => { const f = a.map(num).filter(v => v !== null); return f.length ? Math.max.apply(null, f) : null; };
  const min = a => { const f = a.map(num).filter(v => v !== null); return f.length ? Math.min.apply(null, f) : null; };
  const avg = a => { const f = a.map(num).filter(v => v !== null); return f.length ? f.reduce((s, v) => s + v, 0) / f.length : null; };
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  /** Picks the day to score: today until 14:00 JST, tomorrow after that. */
  function targetDay(now) {
    const h = jstHourString(now);
    const hour = parseInt(h.slice(11, 13), 10);
    const d = new Date((now ? now.getTime() : Date.now()) + 9 * 3600e3);
    if (hour >= 14) d.setUTCDate(d.getUTCDate() + 1);
    return { date: d.toISOString().slice(0, 10), isTomorrow: hour >= 14 };
  }

  function indexOfTime(times, iso) { return times.indexOf(iso); }

  function windowSlice(h, key, from, to) { return h[key] ? h[key].slice(Math.max(0, from), Math.max(0, to)) : []; }

  /** Weather summary for one resort for the ski window (08:00-16:00) of the target day. */
  function summarize(series, day) {
    const t = series.top.time;
    const open = indexOfTime(t, day + 'T08:00');
    const close = indexOfTime(t, day + 'T16:00');
    if (open < 0 || close < 0) return null;
    const T = series.top, B = series.base;
    return {
      snow24: sum(windowSlice(T, 'snowfall', open - 24, open)),       // overnight + previous day, cm
      snow72: sum(windowSlice(T, 'snowfall', open - 72, open)),
      snowDuring: sum(windowSlice(T, 'snowfall', open, close)),
      gustMax: max(windowSlice(T, 'wind_gusts_10m', open, close)),
      windMax: max(windowSlice(T, 'wind_speed_10m', open, close)),
      windDir: avgDirection(windowSlice(T, 'wind_direction_10m', open, close)),
      tempTop: avg(windowSlice(T, 'temperature_2m', open, close)),
      tempBase: avg(windowSlice(B, 'temperature_2m', open, close)),
      precipBase: sum(windowSlice(B, 'precipitation', open, close)),
      snowBase: sum(windowSlice(B, 'snowfall', open, close)),
      visMin: min(windowSlice(T, 'visibility', open, close)),
      freezing: avg(windowSlice(T, 'freezing_level_height', open, close)),
      freezing24hAgo: avg(windowSlice(T, 'freezing_level_height', open - 24, open - 16)),
      code: max(windowSlice(T, 'weather_code', open, close))
    };
  }

  function avgDirection(dirs) {
    const f = dirs.map(num).filter(v => v !== null);
    if (!f.length) return null;
    let x = 0, y = 0;
    f.forEach(d => { x += Math.cos(d * Math.PI / 180); y += Math.sin(d * Math.PI / 180); });
    return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
  }

  const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
  function compass(deg) { return deg === null ? '—' : COMPASS[Math.round(deg / 45) % 8]; }

  /**
   * Resort Mode score (0-100). Components:
   *  raw: base 30 · fresh snow ≤40 (diminishing) · snow quality ±15 · wind ≤25 (minus hold penalties)
   *  · shelter bonus on stormy days · vertical bonus on calm days · live lift ratio if available.
 *  Raw max ≈125, rescaled to 0-100.
   */
  function scoreResort(r, w, liveLifts) {
    const reasons = [];
    const flags = [];
    let score = 30;

    // 1) Fresh snow (orographic bias applied)
    const fresh = (w.snow24 || 0) * r.snowBias;
    const falling = (w.snowDuring || 0) * r.snowBias;
    const powder = 40 * (1 - Math.exp(-(fresh + 0.5 * falling) / 20));  // diminishing returns: 20 cm ≈ 25 pts, 50 cm ≈ 37
    score += powder;
    if (fresh >= 1) reasons.push({ t: Math.round(fresh) + ' cm fresh overnight', good: true });
    if (falling >= 3) reasons.push({ t: 'Still snowing: +' + Math.round(falling) + ' cm in ski hours', good: true });

    // 2) Snow quality from summit/base temperature
    if (w.tempTop !== null) {
      if (w.tempTop <= -6) { score += 15; reasons.push({ t: 'Cold, dry snow up top (' + Math.round(w.tempTop) + '°C)', good: true }); }
      else if (w.tempTop <= -2) score += 8;
      else if (w.tempTop > 0) { score -= 10; reasons.push({ t: 'Summit above freezing — heavy or wet snow', good: false }); }
    }
    if (w.tempBase !== null && w.tempBase > 1 && w.precipBase > 1 && w.snowBase < w.precipBase * 0.5) {
      score -= 10; flags.push('rain-base');
      reasons.push({ t: 'Rain likely at the base', good: false });
    }

    // 3) Wind & likely lift holds
    const gust = w.gustMax || 0;
    const windRange = Math.max(10, r.fullHoldGust - 25);
    score += 25 * (1 - clamp((gust - 25) / windRange, 0, 1));
    let holdState = 'none';
    if (gust >= r.fullHoldGust) {
      holdState = 'full'; flags.push('wind-full');
      reasons.push({ t: 'Gusts ' + Math.round(gust) + ' km/h — most lifts may stop', good: false });
    } else if (gust >= r.upperHoldGust) {
      holdState = 'upper'; flags.push('wind-upper');
      score -= 3 * r.windExposure;
      reasons.push({ t: 'Gusts ' + Math.round(gust) + ' km/h — upper lifts may hold', good: false });
    }

    // 4) Shelter: trees win when it's windy, snowing hard or foggy
    const stormy = gust >= 45 || (w.visMin !== null && w.visMin < 1500) || falling >= 5;
    if (stormy && r.treeLevel >= 2) { score += r.treeLevel * 5; reasons.push({ t: 'Tree-sheltered terrain for low visibility', good: true }); }
    if (stormy && r.windExposure >= 4 && holdState === 'none') score -= 4;

    // 5) Vertical is only a bonus if the top is actually reachable
    const topReach = holdState === 'none' ? r.top : r.fallbackTop;
    const vert = Math.max(0, topReach - r.base);
    if (!stormy) score += clamp(vert / 100, 0, 10);

    // 6) Live lift status (scripts/update_lifts.py) overrides guesswork
    if (liveLifts && liveLifts.total > 0) {
      const ratio = liveLifts.open / liveLifts.total;
      if (ratio < 0.3) { score *= 0.4; reasons.push({ t: 'Only ' + liveLifts.open + '/' + liveLifts.total + ' lifts running now', good: false }); }
      else if (ratio < 0.7) { score *= 0.8; reasons.push({ t: liveLifts.open + '/' + liveLifts.total + ' lifts running now', good: false }); }
      else reasons.push({ t: liveLifts.open + '/' + liveLifts.total + ' lifts running', good: true });
    }

    if (holdState === 'full') score *= 0.35;
    score = score * 100 / 125;  // theoretical max ≈125 → 0-100 scale
    return { id: r.id, score: Math.round(clamp(score, 0, 100)), reasons: reasons.slice(0, 4), flags: flags, holdState: holdState, weather: w, skiableVert: vert };
  }

  function rankResorts(norm, resorts, lifts, now) {
    const day = targetDay(now);
    // Live lift status only describes *right now*: use it for today's picks, and only once the valley is running
    // (at night or before opening every lift reads closed, which would wrongly sink every resort).
    const valleyRunning = lifts && Object.keys(lifts).some(k => lifts[k] && lifts[k].open > 0);
    if (day.isTomorrow || !valleyRunning) lifts = null;
    const ranked = [];
    resorts.forEach(r => {
      if (!norm[r.id]) return;
      const w = summarize(norm[r.id], day.date);
      if (!w) return;
      ranked.push(Object.assign(scoreResort(r, w, lifts && lifts[r.id]), { resort: r }));
    });
    ranked.sort((a, b) => b.score - a.score);
    return { day: day, ranked: ranked };
  }

  /** Aspects that receive wind-blown snow = lee of the wind (wind FROM dir loads the opposite aspects). */
  function leeAspects(fromDeg) {
    if (fromDeg === null) return [];
    const lee = (fromDeg + 180) % 360;
    return [-45, 0, 45].map(o => compass((lee + o + 360) % 360));
  }

  /** Backcountry Mode: elevation bands + weather-derived red flags (NOT an avalanche rating). */
  function backcountry(norm, resorts, now) {
    const day = targetDay(now);
    const ref = norm.happo || norm[resorts[0].id];
    const T = ref.top;
    const i0 = T.time.indexOf(day.date + 'T08:00');
    const i1 = T.time.indexOf(day.date + 'T14:00');
    if (i0 < 0) return null;
    const band = (label, elev, sp, dir, temp) => ({
      label: label, elev: elev,
      wind: avg(windowSlice(T, sp, i0, i1)), gust: null,
      dir: avgDirection(windowSlice(T, dir, i0, i1)),
      temp: avg(windowSlice(T, temp, i0, i1))
    });
    const ridge = band('Alpine ridges', '~3,000 m (700 hPa)', 'wind_speed_700hPa', 'wind_direction_700hPa', 'temperature_700hPa');
    const tree = band('Treeline', '~1,500 m (850 hPa)', 'wind_speed_850hPa', 'wind_direction_850hPa', 'temperature_850hPa');
    const top = summarize(ref, day.date);
    const resortTop = { label: 'Happo summit', elev: '1,831 m', wind: top.windMax, gust: top.gustMax, dir: top.windDir, temp: top.tempTop };

    // valley-wide new snow: max across north/central resort tops
    const n72 = Math.max.apply(null, resorts.filter(r => r.area !== 'south' && norm[r.id]).map(r => {
      const s = summarize(norm[r.id], day.date); return s ? s.snow72 * r.snowBias : 0;
    }));
    const n24 = Math.max.apply(null, resorts.filter(r => r.area !== 'south' && norm[r.id]).map(r => {
      const s = summarize(norm[r.id], day.date); return s ? s.snow24 * r.snowBias : 0;
    }));

    const flags = [];
    if (n24 >= 30) flags.push({ level: 'high', t: 'Heavy loading: ~' + Math.round(n24) + ' cm in 24 h. New storm slabs are likely.' });
    else if (n72 >= 40) flags.push({ level: 'mid', t: '~' + Math.round(n72) + ' cm in 72 h. The new snow needs time to bond.' });
    const transportWind = Math.max(ridge.wind || 0, tree.wind || 0);
    if (transportWind >= 25 && n72 >= 10) flags.push({ level: 'high', t: 'Wind transport: ' + Math.round(transportWind) + ' km/h with new snow available. Expect wind slabs on ' + leeAspects(ridge.dir).join(' / ') + ' aspects.' });
    if (top.freezing !== null && top.freezing24hAgo !== null && top.freezing - top.freezing24hAgo > 500) flags.push({ level: 'mid', t: 'Rapid warming: freezing level up ' + Math.round(top.freezing - top.freezing24hAgo) + ' m in 24 h.' });
    if (top.freezing !== null && top.freezing > 1500 && top.snow72 > 10) flags.push({ level: 'mid', t: 'Freezing level ~' + Math.round(top.freezing) + ' m on recent snow — expect wet snow and moist layers below it.' });
    if (top.visMin !== null && top.visMin < 1000) flags.push({ level: 'mid', t: 'Low visibility in the alpine (<1 km). Navigation will be hard.' });
    if (!flags.length) flags.push({ level: 'low', t: 'No major weather red flags in the model. Snowpack can still be unstable — read the JAN bulletin.' });

    return { day: day, bands: [ridge, resortTop, tree], freezing: top.freezing, new24: n24, new72: n72, leeAspects: leeAspects(ridge.dir), flags: flags };
  }

  const api = { HOURLY_VARS, buildOpenMeteoUrl, normalize, summarize, scoreResort, rankResorts, backcountry, compass, leeAspects, jstHourString, targetDay };
  if (typeof module !== 'undefined' && module.exports) module.exports = api; else root.Engine = api;
})(typeof window !== 'undefined' ? window : globalThis);

// Country-code → ISO 4217 currency mapping
const COUNTRY_CURRENCY: Record<string, string> = {
  // Africa
  BW: 'BWP', ET: 'ETB', GH: 'GHS', KE: 'KES', MW: 'MWK',
  MZ: 'MZN', NG: 'NGN', RW: 'RWF', TZ: 'TZS', UG: 'UGX',
  ZA: 'ZAR', ZM: 'ZMW', ZW: 'ZWL',
  // Anglosphere
  AU: 'AUD', CA: 'CAD', GB: 'GBP', HK: 'HKD', NZ: 'NZD',
  SG: 'SGD', US: 'USD',
  // Asia-Pacific
  CN: 'CNY', IN: 'INR', JP: 'JPY', KR: 'KRW', MY: 'MYR',
  PH: 'PHP', TH: 'THB',
  // Americas
  AR: 'ARS', BR: 'BRL', MX: 'MXN',
  // Europe (non-euro)
  CH: 'CHF', CZ: 'CZK', DK: 'DKK', HU: 'HUF', NO: 'NOK',
  PL: 'PLN', RO: 'RON', SE: 'SEK',
  // Euro zone
  AT: 'EUR', BE: 'EUR', CY: 'EUR', DE: 'EUR', EE: 'EUR',
  ES: 'EUR', FI: 'EUR', FR: 'EUR', GR: 'EUR', IE: 'EUR',
  IT: 'EUR', LT: 'EUR', LU: 'EUR', LV: 'EUR', MT: 'EUR',
  NL: 'EUR', PT: 'EUR', SI: 'EUR', SK: 'EUR',
}

// IANA timezone city → country code (for countries in COUNTRY_CURRENCY)
const TIMEZONE_COUNTRY: Record<string, string> = {
  // Africa
  'Africa/Blantyre': 'MW', 'Africa/Gaborone': 'BW', 'Africa/Addis_Ababa': 'ET',
  'Africa/Accra': 'GH', 'Africa/Nairobi': 'KE', 'Africa/Maputo': 'MZ',
  'Africa/Lagos': 'NG', 'Africa/Kigali': 'RW', 'Africa/Dar_es_Salaam': 'TZ',
  'Africa/Kampala': 'UG', 'Africa/Johannesburg': 'ZA', 'Africa/Lusaka': 'ZM',
  'Africa/Harare': 'ZW',
  // Anglosphere
  'Australia/Sydney': 'AU', 'Australia/Melbourne': 'AU', 'Australia/Perth': 'AU',
  'America/Toronto': 'CA', 'America/Vancouver': 'CA',
  'Europe/London': 'GB',
  'Asia/Hong_Kong': 'HK', 'Pacific/Auckland': 'NZ', 'Asia/Singapore': 'SG',
  'America/New_York': 'US', 'America/Chicago': 'US', 'America/Denver': 'US',
  'America/Los_Angeles': 'US', 'America/Phoenix': 'US',
  // Asia-Pacific
  'Asia/Shanghai': 'CN', 'Asia/Kolkata': 'IN', 'Asia/Tokyo': 'JP',
  'Asia/Seoul': 'KR', 'Asia/Kuala_Lumpur': 'MY', 'Asia/Manila': 'PH',
  'Asia/Bangkok': 'TH',
  // Americas
  'America/Argentina/Buenos_Aires': 'AR', 'America/Sao_Paulo': 'BR',
  'America/Mexico_City': 'MX',
  // Europe
  'Europe/Zurich': 'CH', 'Europe/Prague': 'CZ', 'Europe/Copenhagen': 'DK',
  'Europe/Budapest': 'HU', 'Europe/Oslo': 'NO', 'Europe/Warsaw': 'PL',
  'Europe/Bucharest': 'RO', 'Europe/Stockholm': 'SE',
  'Europe/Vienna': 'AT', 'Europe/Brussels': 'BE', 'Europe/Berlin': 'DE',
  'Europe/Tallinn': 'EE', 'Europe/Madrid': 'ES', 'Europe/Helsinki': 'FI',
  'Europe/Paris': 'FR', 'Europe/Athens': 'GR', 'Europe/Dublin': 'IE',
  'Europe/Rome': 'IT', 'Europe/Vilnius': 'LT', 'Europe/Luxembourg': 'LU',
  'Europe/Riga': 'LV', 'Europe/Amsterdam': 'NL', 'Europe/Lisbon': 'PT',
  'Europe/Ljubljana': 'SI', 'Europe/Bratislava': 'SK',
}

function detectCurrency(): string {
  // The OS timezone is the best indicator of the local operating currency when
  // the UI language differs from the user's location (for example, en-GB in Malawi).
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
    const country = TIMEZONE_COUNTRY[tz]
    if (country && COUNTRY_CURRENCY[country]) return COUNTRY_CURRENCY[country]
  } catch {
    // ignore — Intl not available
  }

  // Fall back to browser language tags, which can still provide a useful
  // country signal when timezone information is unavailable.
  const langs = [...(navigator.languages ?? []), navigator.language ?? 'en-US']
  for (const lang of langs) {
    const parts = lang.split('-')
    const country = parts[parts.length - 1].toUpperCase()
    if (COUNTRY_CURRENCY[country]) return COUNTRY_CURRENCY[country]
  }

  return 'USD'
}

function detectLocalLocale(): string {
  try {
    const country = TIMEZONE_COUNTRY[Intl.DateTimeFormat().resolvedOptions().timeZone]
    if (country) return `en-${country}`
  } catch {
    // ignore — Intl not available
  }
  return (navigator.languages && navigator.languages[0]) || navigator.language || 'en-US'
}

const locale = detectLocalLocale()
const currency = detectCurrency()

const _fmt = new Intl.NumberFormat(locale, {
  style: 'currency',
  currency,
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})
const _compact = new Intl.NumberFormat(locale, {
  style: 'currency', currency, notation: 'compact', maximumFractionDigits: 2,
})

/** Format a monetary amount in the system currency. */
export function fmt(amount: number): string {
  return Math.abs(amount) >= 1_000_000 ? _compact.format(amount) : _fmt.format(amount)
}

/** Format P&L — prepends "+" for positive values. */
export function fmtPnl(amount: number): string {
  return (amount >= 0 ? '+' : '') + (Math.abs(amount) >= 1_000_000 ? _compact.format(amount) : _fmt.format(amount))
}

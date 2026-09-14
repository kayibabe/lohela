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

function detectCurrency(): string {
  const lang =
    (navigator.languages && navigator.languages[0]) ||
    navigator.language ||
    'en-US'
  const parts = lang.split('-')
  const country = parts[parts.length - 1].toUpperCase()
  return COUNTRY_CURRENCY[country] ?? 'USD'
}

const locale = (navigator.languages && navigator.languages[0]) || navigator.language || 'en-US'
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

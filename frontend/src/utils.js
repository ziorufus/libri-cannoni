const dateFmt = new Intl.DateTimeFormat('it-IT', { dateStyle: 'short', timeStyle: 'short' })
const relFmt = new Intl.RelativeTimeFormat('it-IT', { numeric: 'auto' })

export function fmtDate(value) {
  return value ? dateFmt.format(new Date(value)) : '—'
}

export function fmtRelative(value) {
  if (!value) return '—'
  const diff = (new Date(value) - Date.now()) / 1000
  const abs = Math.abs(diff)
  if (abs < 60) return relFmt.format(Math.round(diff), 'second')
  if (abs < 3600) return relFmt.format(Math.round(diff / 60), 'minute')
  if (abs < 86400) return relFmt.format(Math.round(diff / 3600), 'hour')
  return relFmt.format(Math.round(diff / 86400), 'day')
}

export function fmtBytes(n) {
  if (!n) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)))
  return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`
}

export const STATUS_STYLE = {
  ready: { cls: 'text-bg-secondary', icon: 'bi-hourglass' },
  running: { cls: 'text-bg-primary', icon: 'bi-arrow-repeat spin' },
  waiting: { cls: 'text-bg-warning', icon: 'bi-clock-history' },
  failed: { cls: 'text-bg-danger', icon: 'bi-exclamation-triangle' },
  idle: { cls: 'text-bg-success', icon: 'bi-check2' },
}

const dateTimeFormat = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

const timeFormat = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

/** 한국 시간 MM. DD. HH:mm:ss */
export function formatDateTime(value: Date | string | null | undefined) {
  if (!value) return "-";
  return dateTimeFormat.format(new Date(value));
}

/** 한국 시간 HH:mm:ss */
export function formatTime(value: Date | string | null | undefined) {
  if (!value) return "-";
  return timeFormat.format(new Date(value));
}

export function formatNumber(value: number | null | undefined) {
  return value == null ? "-" : value.toLocaleString("ko-KR");
}

export function formatPrice(value: number | null | undefined) {
  return value == null ? "-" : `${value.toLocaleString("ko-KR")}원`;
}

/** searchParams의 page 값을 1 이상의 정수로 정규화한다. */
export function parsePage(value: string | string[] | undefined) {
  const page = Number(Array.isArray(value) ? value[0] : value);
  return Number.isInteger(page) && page > 0 ? page : 1;
}

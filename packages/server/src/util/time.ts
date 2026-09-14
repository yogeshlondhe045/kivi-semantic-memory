/**
 * Time expressions in speech are vague ("around 5PM yesterday", "last Tuesday",
 * "this morning"). We resolve them to explicit windows so retrieval can filter,
 * and so the window can be shown back to the user and argued with.
 */

export const MINUTE = 60_000;
export const HOUR = 60 * MINUTE;
export const DAY = 24 * HOUR;

export interface TimeWindow {
  from: number | null;
  to: number | null;
  label: string | null;
  /** The moment the phrase actually named, when it named one. */
  centre?: number | null;
  /** How tightly the phrase pins the moment. Feeds the recency prior. */
  precision: 'none' | 'day' | 'part_of_day' | 'hour';
}

const NONE: TimeWindow = { from: null, to: null, label: null, precision: 'none' };

const WEEKDAYS = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];

function startOfDay(ts: number, tzOffsetMinutes: number): number {
  const shifted = ts + tzOffsetMinutes * MINUTE;
  const floored = Math.floor(shifted / DAY) * DAY;
  return floored - tzOffsetMinutes * MINUTE;
}

export interface TimeParseOptions {
  now?: number;
  /** Minutes east of UTC. Kivi's demo user is in IST (+330). */
  tzOffsetMinutes?: number;
}

export function parseTimeExpression(text: string, options: TimeParseOptions = {}): TimeWindow {
  const now = options.now ?? Date.now();
  const tz = options.tzOffsetMinutes ?? 330;
  const q = text.toLowerCase();
  const today = startOfDay(now, tz);

  const hourMatch =
    /\baround\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b/.exec(q) ??
    /\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b/.exec(q) ??
    /\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b/.exec(q);

  let dayStart: number | null = null;
  let dayLabel = '';

  if (/\byesterday\b/.test(q)) {
    dayStart = today - DAY;
    dayLabel = 'yesterday';
  } else if (/\btoday\b|\bthis (morning|afternoon|evening)\b|\btonight\b/.test(q)) {
    dayStart = today;
    dayLabel = 'today';
  } else if (/\bday before yesterday\b/.test(q)) {
    dayStart = today - 2 * DAY;
    dayLabel = 'the day before yesterday';
  } else {
    const weekday = WEEKDAYS.findIndex((d) => new RegExp(`\\b(last\\s+)?${d}\\b`).test(q));
    if (weekday >= 0) {
      const nowDow = new Date(now + tz * MINUTE).getUTCDay();
      let back = (nowDow - weekday + 7) % 7;
      if (back === 0) back = 7;
      dayStart = today - back * DAY;
      dayLabel = `last ${WEEKDAYS[weekday]}`;
    }
  }

  if (dayStart !== null && hourMatch) {
    let hour = Number(hourMatch[1]);
    const minutes = Number(hourMatch[2] ?? 0);
    const meridiem = hourMatch[3];
    if (meridiem === 'pm' && hour < 12) hour += 12;
    if (meridiem === 'am' && hour === 12) hour = 0;
    if (!meridiem && hour < 8) hour += 12; // "around 5" in a workday almost always means 17:00
    const centre = dayStart + hour * HOUR + minutes * MINUTE;
    // "around" is a soft constraint: a 90-minute window either side, widened by
    // the scorer rather than treated as a hard cut-off.
    return {
      from: centre - 90 * MINUTE,
      to: centre + 90 * MINUTE,
      centre,
      label: `${dayLabel} around ${formatHour(hour, minutes)}`,
      precision: 'hour',
    };
  }

  if (dayStart !== null) {
    const partOfDay = partOfDayWindow(q, dayStart);
    if (partOfDay) return { ...partOfDay, label: `${dayLabel} ${partOfDay.label}`, precision: 'part_of_day' };
    return { from: dayStart, to: dayStart + DAY, label: dayLabel, precision: 'day' };
  }

  if (/\blast week\b/.test(q)) {
    return { from: today - 7 * DAY, to: today + DAY, label: 'the last week', precision: 'day' };
  }
  if (/\bthis week\b/.test(q)) {
    const dow = new Date(now + tz * MINUTE).getUTCDay();
    return { from: today - dow * DAY, to: today + DAY, label: 'this week', precision: 'day' };
  }
  if (/\blast month\b|\bpast month\b/.test(q)) {
    return { from: today - 30 * DAY, to: today + DAY, label: 'the last month', precision: 'day' };
  }
  if (/\brecently\b|\blately\b|\bthese days\b/.test(q)) {
    return { from: today - 14 * DAY, to: today + DAY, label: 'recently', precision: 'day' };
  }

  return NONE;
}

function partOfDayWindow(q: string, dayStart: number): (TimeWindow & { label: string }) | null {
  if (/\bmorning\b/.test(q)) return { from: dayStart + 5 * HOUR, to: dayStart + 12 * HOUR, label: 'morning', precision: 'part_of_day' };
  if (/\bafternoon\b/.test(q)) return { from: dayStart + 12 * HOUR, to: dayStart + 17 * HOUR, label: 'afternoon', precision: 'part_of_day' };
  if (/\bevening\b|\btonight\b/.test(q)) return { from: dayStart + 17 * HOUR, to: dayStart + 23 * HOUR, label: 'evening', precision: 'part_of_day' };
  return null;
}

function formatHour(hour24: number, minutes: number): string {
  const suffix = hour24 >= 12 ? 'PM' : 'AM';
  const h = hour24 % 12 === 0 ? 12 : hour24 % 12;
  return minutes ? `${h}:${String(minutes).padStart(2, '0')} ${suffix}` : `${h} ${suffix}`;
}

/** Parses "by Friday", "before the 14th", "next Tuesday" into a due timestamp. */
export function parseDueDate(text: string, now = Date.now(), tzOffsetMinutes = 330): number | null {
  const q = text.toLowerCase();
  const today = startOfDay(now, tzOffsetMinutes);
  const dow = new Date(now + tzOffsetMinutes * MINUTE).getUTCDay();

  if (/\btomorrow\b/.test(q)) return today + DAY;
  if (/\btoday\b|\bend of (the )?day\b|\beod\b/.test(q)) return today + DAY - 1;
  if (/\bend of (the )?week\b|\beow\b/.test(q)) return today + ((5 - dow + 7) % 7) * DAY;
  if (/\bnext week\b/.test(q)) return today + (8 - dow) * DAY;

  const weekday = WEEKDAYS.findIndex((d) => new RegExp(`\\b(by|before|on|next)\\s+${d}\\b`).test(q));
  if (weekday >= 0) {
    let ahead = (weekday - dow + 7) % 7;
    if (ahead === 0) ahead = 7;
    return today + ahead * DAY;
  }
  return null;
}

export function formatTimestamp(ts: number, tzOffsetMinutes = 330): string {
  const d = new Date(ts + tzOffsetMinutes * MINUTE);
  const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const hh = d.getUTCHours();
  const mm = String(d.getUTCMinutes()).padStart(2, '0');
  const suffix = hh >= 12 ? 'PM' : 'AM';
  const h12 = hh % 12 === 0 ? 12 : hh % 12;
  return `${days[d.getUTCDay()]} ${d.getUTCDate()} ${months[d.getUTCMonth()]}, ${h12}:${mm} ${suffix}`;
}

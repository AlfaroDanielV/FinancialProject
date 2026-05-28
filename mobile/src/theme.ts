/**
 * Design system — Bauhaus-inspired, earth tones, warm parchment palette.
 *
 * Principles applied throughout the app:
 *   - Form follows function. No decorative chrome.
 *   - Details on demand (expandable sections, not everything upfront).
 *   - Red is reserved for errors and overdue states only.
 *   - Typography and spacing carry hierarchy; color is secondary.
 */

export const Colors = {
  // ── backgrounds ───────────────────────────────────────────────────────────
  bg: "#F5F0E8",         // Main — warm parchment / old paper
  bgCard: "#EDE8DC",     // Card surface — slightly deeper
  bgElevated: "#E6DDD0", // Inset / pressed / inner panel
  bgInput: "#EAE4D5",    // Text input fill

  // ── borders & dividers ────────────────────────────────────────────────────
  border: "#C9BBA2",     // Standard border
  borderLight: "#DDD6C4",// Subtle separator

  // ── text ──────────────────────────────────────────────────────────────────
  textPrimary: "#2B1F10",   // Near-black ink on parchment
  textSecondary: "#6B5A3E", // Secondary warm brown
  textMuted: "#9C8B74",     // Labels, placeholders, hints
  textOnDark: "#FAF8F3",    // Text on accent / dark surfaces

  // ── accent — forest green ─────────────────────────────────────────────────
  accent: "#4A6741",        // Primary action (buttons, active tabs)
  accentSoft: "#8DA887",    // Secondary / lighter green
  accentBg: "#E8EEE6",      // Tinted green surface (tags, active pills)

  // ── semantic ──────────────────────────────────────────────────────────────
  income: "#3D5C35",        // Green — positive values, income
  expense: "#8B3A2A",       // Deep brick red — negative values (use sparingly)
  warning: "#7A6228",       // Ochre — caution state
  warningBg: "#F0E8D0",     // Warm amber tint background
  overdue: "#8B3A2A",       // Same as expense — overdue bills

  // ── utility ───────────────────────────────────────────────────────────────
  overlay: "rgba(43,31,16,0.06)", // Pressed-state scrim
} as const;

export const Spacing = {
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 32,
} as const;

export const FontSize = {
  xs: 11,
  sm: 13,
  md: 15,
  lg: 18,
  xl: 24,
  display: 36,
} as const;

export const Radius = {
  sm: 4,
  md: 8,
  lg: 12,
} as const;

// Consistent card shadow — subtle, warm-toned.
export const CardShadow = {
  shadowColor: "#2B1F10",
  shadowOffset: { width: 0, height: 1 },
  shadowOpacity: 0.08,
  shadowRadius: 4,
  elevation: 2,
} as const;

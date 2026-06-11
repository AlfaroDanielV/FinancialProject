/**
 * Design system v2 (Phase 7c "UI 2.0") — neutral, Rams/Braun-inspired.
 *
 * Principles applied throughout the app:
 *   - Form follows function. No decorative chrome.
 *   - Neutral surfaces (off-white / white / graphite ink); color is reserved
 *     for MEANING: green = income, red = expense/overdue, ochre = caution,
 *     class hues on envelope bars. The primary accent is ink, not a brand hue.
 *   - Typography and spacing carry hierarchy. Money is always tabular.
 *   - Details on demand (expandable sections, not everything upfront).
 */

export const Colors = {
  // ── backgrounds ───────────────────────────────────────────────────────────
  bg: "#F7F7F4",         // App canvas — soft neutral off-white
  bgCard: "#FFFFFF",     // Card surface
  bgElevated: "#F1F1ED", // Inset / pressed / inner panel
  bgInput: "#F3F3EF",    // Text input fill

  // ── borders & dividers ────────────────────────────────────────────────────
  border: "#DDDDD6",     // Standard border
  borderLight: "#EBEBE5",// Subtle separator

  // ── text ──────────────────────────────────────────────────────────────────
  textPrimary: "#191917",   // Graphite ink
  textSecondary: "#52524C", // Secondary gray
  textMuted: "#8B8B83",     // Labels, placeholders, hints
  textOnDark: "#FBFBF9",    // Text on accent / dark surfaces

  // ── accent — ink ──────────────────────────────────────────────────────────
  accent: "#191917",        // Primary action (buttons, active tabs) — ink
  accentSoft: "#6F6F68",    // Secondary emphasis
  accentBg: "#EDEDE8",      // Tinted surface (tags, active pills)

  // ── semantic ──────────────────────────────────────────────────────────────
  income: "#2E7D54",        // Calm green — positive values, income
  expense: "#B3402E",       // Restrained brick red — negative values only
  warning: "#8A6A1F",       // Ochre — caution state
  warningBg: "#F5EEDD",     // Warm tint background
  overdue: "#B3402E",       // Same as expense — overdue bills

  // ── utility ───────────────────────────────────────────────────────────────
  overlay: "rgba(25,25,23,0.05)", // Pressed-state scrim
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
  hero: 40,
} as const;

/**
 * Inter (static weights, loaded in App.tsx via expo-font). Use `fontFamily`
 * with these names and do NOT also set `fontWeight` — static font files carry
 * one weight each and iOS may otherwise synthesize a fake bold.
 *
 * Rollout: display/money text and the Inicio screen use Inter; body text on
 * older screens stays on system San Francisco until each screen is touched.
 */
export const Fonts = {
  regular: "Inter-Regular",
  medium: "Inter-Medium",
  semibold: "Inter-SemiBold",
  bold: "Inter-Bold",
} as const;

export const Radius = {
  sm: 6,
  md: 10,
  lg: 14,
} as const;

// Consistent card shadow — barely-there, neutral.
export const CardShadow = {
  shadowColor: "#191917",
  shadowOffset: { width: 0, height: 2 },
  shadowOpacity: 0.05,
  shadowRadius: 8,
  elevation: 2,
} as const;

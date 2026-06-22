import * as LocalAuthentication from "expo-local-authentication";

/**
 * Biometric / passcode app lock helpers (Face ID, Touch ID, or device
 * passcode). Thin wrappers around `expo-local-authentication` that never throw
 * — a failure to even ask is treated as "can't gate" so the user is never
 * stranded out of their own finance app.
 *
 * Expo Go note: on iOS, Face ID does NOT engage inside Expo Go (Expo Go's
 * Info.plist has no `NSFaceIDUsageDescription`), so `authenticateAsync` falls
 * back to the device passcode prompt there — still a real lock. A dev /
 * standalone build that carries the Info.plist key (see app.json) shows real
 * Face ID with this exact same code.
 */

/**
 * True only when the device can actually authenticate the user (enrolled
 * biometrics OR a device passcode). When there is NO auth method at all we must
 * not lock — otherwise the user could never get back in. The actual prompt
 * (`promptBiometric`) keeps `disableDeviceFallback: false`, so a passcode-only
 * device still unlocks.
 */
export async function biometricGateAvailable(): Promise<boolean> {
  try {
    const level = await LocalAuthentication.getEnrolledLevelAsync();
    return level !== LocalAuthentication.SecurityLevel.NONE;
  } catch {
    return false;
  }
}

/**
 * Prompt for Face ID / Touch ID, falling back to the device passcode. Returns
 * true on success, false on failure/cancel (the caller stays locked and shows a
 * retry — we never sign the user out on a failed prompt).
 */
export async function promptBiometric(): Promise<boolean> {
  try {
    const result = await LocalAuthentication.authenticateAsync({
      promptMessage: "Desbloqueá Ledger CR",
      cancelLabel: "Cancelar",
      disableDeviceFallback: false,
    });
    return result.success;
  } catch {
    return false;
  }
}

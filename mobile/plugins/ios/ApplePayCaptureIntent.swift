// ApplePayCaptureIntent.swift
//
// Zero-touch contactless capture for Ledger CR. An iOS Shortcuts "Wallet"
// (a.k.a. "Transaction") trigger automation passes the Apple Pay Amount +
// Merchant into this App Intent, which POSTs them to the Ledger CR backend so
// the purchase lands in the ledger with no taps.
//
// Compiled into the MAIN app target (not a separate extension), so it runs
// in-process and shares the app's keychain — it reads the session bearer token
// that the JS app wrote via expo-secure-store (see src/lib/appIntentToken.ts).
//
// Hard rules honored here:
//   • No LLM on this path — iOS already delivers structured fields.
//   • Decimal-safe: amount is parsed locale-aware, sent as a canonical string;
//     the backend re-validates to Decimal (never float).
//   • Idempotent: a per-tap UUID (client_event_id) makes an offline retry a
//     no-op server-side (source_ref UNIQUE).
//   • es-CR voseo for any user-facing dialog.
//
// This file is injected by mobile/plugins/withApplePayIntent.js at prebuild.
// It cannot be compiled/tested in Expo Go — it ships only in an EAS dev /
// TestFlight build. On-device verification is the only guard (no native CI).

import AppIntents
import Foundation
import Security

// MARK: - Configuration

private enum LedgerConfig {
    /// The keychain services the JS app's token may live under. IMPORTANT:
    /// expo-secure-store APPENDS ":no-auth" / ":auth" to the configured
    /// `keychainService` based on `requireAuthentication`
    /// (SecureStoreModule.swift: `service.append(":\(... ? "auth" : "no-auth")")`).
    /// src/lib/appIntentToken.ts writes with requireAuthentication=false, so the
    /// item lands under "...:no-auth". We query the candidates in order
    /// (no-auth → auth → bare legacy) so this stays correct across
    /// expo-secure-store versions. Querying by SERVICE alone is robust against
    /// however expo-secure-store names the account attribute internally.
    static let keychainServiceCandidates = [
        "ledgercr.appintent:no-auth",
        "ledgercr.appintent:auth",
        "ledgercr.appintent",
    ]

    /// App-group + standard UserDefaults key for the offline retry queue.
    static let pendingQueueKey = "ledgercr.applepay.pendingQueue"

    /// Injected into Info.plist by the config plugin from
    /// EXPO_PUBLIC_API_BASE_URL. Falls back to the dev default.
    static var apiBaseURL: String {
        let raw = Bundle.main.object(forInfoDictionaryKey: "LedgerApiBaseUrl") as? String
        let base = (raw?.isEmpty == false ? raw! : "http://localhost:8000")
        return base.hasSuffix("/") ? String(base.dropLast()) : base
    }

    static var captureEndpoint: URL? {
        URL(string: "\(apiBaseURL)/api/v1/transactions/apple-pay")
    }
}

// MARK: - Keychain

private enum LedgerKeychain {
    /// Reads the session bearer token. Tries each candidate service (matching by
    /// service only — robust against expo-secure-store's internal account
    /// naming) and returns the first non-empty value. expo-secure-store stores
    /// the value as plaintext keychain Data (no app-level encryption envelope),
    /// so the bytes ARE the JWT.
    static func sessionToken() -> String? {
        for service in LedgerConfig.keychainServiceCandidates {
            let query: [String: Any] = [
                kSecClass as String: kSecClassGenericPassword,
                kSecAttrService as String: service,
                kSecReturnData as String: true,
                kSecMatchLimit as String: kSecMatchLimitOne,
            ]
            var item: CFTypeRef?
            let status = SecItemCopyMatching(query as CFDictionary, &item)
            if status == errSecSuccess,
               let data = item as? Data,
               let token = String(data: data, encoding: .utf8),
               !token.isEmpty {
                return token
            }
        }
        return nil
    }
}

// MARK: - Money parsing (deterministic, Decimal-safe)

private enum MoneyParser {
    /// Detect CRC vs USD from the localized string. ₡ → CRC, $ → USD; default
    /// CRC (the device is CR-first). Drives the dual-currency leg server-side.
    static func currency(from raw: String) -> String {
        if raw.contains("$") && !raw.contains("₡") { return "USD" }
        return "CRC"
    }

    /// Parse the localized amount to a canonical dot-decimal string. We let
    /// iOS's own NumberFormatter do the locale lifting; on failure we send the
    /// raw string (the backend's parse_money_magnitude also handles the CR
    /// locale form, so this is defense in depth, not the only line of defense).
    static func canonicalAmount(from raw: String) -> String {
        let formatter = NumberFormatter()
        formatter.locale = Locale.current
        formatter.numberStyle = .currency
        if let n = formatter.number(from: raw) {
            return NSDecimalNumber(decimal: n.decimalValue).stringValue
        }
        formatter.numberStyle = .decimal
        let stripped = raw.filter { "0123456789.,".contains($0) }
        if let n = formatter.number(from: stripped) {
            return NSDecimalNumber(decimal: n.decimalValue).stringValue
        }
        // Hand the raw (symbol-trimmed) string to the backend parser.
        return stripped.isEmpty ? raw : stripped
    }
}

// MARK: - Pending-retry queue (offline tolerance)

private struct PendingCapture: Codable {
    let amount: String
    let currency: String
    let merchant: String
    let clientEventId: String
    let cardHint: String?
}

private enum PendingQueue {
    private static let defaults = UserDefaults.standard

    static func load() -> [PendingCapture] {
        guard let data = defaults.data(forKey: LedgerConfig.pendingQueueKey),
              let items = try? JSONDecoder().decode([PendingCapture].self, from: data)
        else { return [] }
        return items
    }

    static func save(_ items: [PendingCapture]) {
        if let data = try? JSONEncoder().encode(items) {
            defaults.set(data, forKey: LedgerConfig.pendingQueueKey)
        }
    }

    static func enqueue(_ item: PendingCapture) {
        var items = load()
        // Idempotent at the queue level too — never enqueue the same tap twice.
        guard !items.contains(where: { $0.clientEventId == item.clientEventId }) else { return }
        items.append(item)
        save(items)
    }

    static func remove(clientEventId: String) {
        save(load().filter { $0.clientEventId != clientEventId })
    }
}

// MARK: - Networking

private enum CaptureResult {
    case ok
    case unauthorized      // 401 — token dead; re-login needed (do not retry)
    case retryable         // network / 5xx — enqueue and try again later
    case rejected          // other 4xx — retrying won't help; drop
}

private enum LedgerAPI {
    static func post(_ capture: PendingCapture, token: String) async -> CaptureResult {
        guard let url = LedgerConfig.captureEndpoint else { return .rejected }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        req.timeoutInterval = 15

        var body: [String: Any] = [
            "amount": capture.amount,
            "currency": capture.currency,
            "merchant": capture.merchant,
            "client_event_id": capture.clientEventId,
        ]
        if let card = capture.cardHint, !card.isEmpty { body["card_hint"] = card }
        guard let data = try? JSONSerialization.data(withJSONObject: body) else {
            return .rejected
        }
        req.httpBody = data

        do {
            let (_, response) = try await URLSession.shared.data(for: req)
            let code = (response as? HTTPURLResponse)?.statusCode ?? 0
            switch code {
            case 200..<300: return .ok
            case 401: return .unauthorized
            case 500..<600: return .retryable
            default: return .rejected
            }
        } catch {
            return .retryable     // no connectivity → retry later
        }
    }

    /// Best-effort flush of previously-failed captures. Stops on the first
    /// retryable failure (likely still offline) to preserve order.
    static func flushPending(token: String) async {
        for item in PendingQueue.load() {
            let result = await post(item, token: token)
            switch result {
            case .ok, .rejected:
                PendingQueue.remove(clientEventId: item.clientEventId)
            case .unauthorized, .retryable:
                return
            }
        }
    }
}

// MARK: - App Intent

struct ApplePayCaptureIntent: AppIntent {
    static var title: LocalizedStringResource = "Registrar compra de Apple Pay"
    static var description = IntentDescription(
        "Registra una compra sin contacto (Apple Pay) en Ledger."
    )
    // Run silently in the background — true zero-touch.
    static var openAppWhenRun: Bool = false

    @Parameter(title: "Monto")
    var amount: String

    @Parameter(title: "Comercio")
    var merchant: String

    @Parameter(title: "Tarjeta")
    var card: String?

    func perform() async throws -> some IntentResult {
        guard let token = LedgerKeychain.sessionToken() else {
            // No session in the keychain → the user hasn't logged in (or logged
            // out). Surface a voseo hint; nothing to retry.
            throw AppIntentError.message(
                "Abrí Ledger e ingresá con /login para registrar tus compras."
            )
        }

        let capture = PendingCapture(
            amount: MoneyParser.canonicalAmount(from: amount),
            currency: MoneyParser.currency(from: amount),
            merchant: merchant,
            clientEventId: UUID().uuidString,
            cardHint: card
        )

        // Drain anything queued from a prior offline run first.
        await LedgerAPI.flushPending(token: token)

        switch await LedgerAPI.post(capture, token: token) {
        case .ok, .rejected:
            return .result()
        case .retryable:
            PendingQueue.enqueue(capture)     // offline → try again next run
            return .result()
        case .unauthorized:
            throw AppIntentError.message(
                "Tu sesión venció. Abrí Ledger y reingresá con /login."
            )
        }
    }
}

/// A simple user-facing error for the few cases worth surfacing.
private enum AppIntentError: Error, CustomLocalizedStringResourceConvertible {
    case message(String)
    var localizedStringResource: LocalizedStringResource {
        switch self { case let .message(text): return "\(text)" }
    }
}

// MARK: - Shortcuts registration

/// Surfaces the intent in Shortcuts / Siri / the Wallet trigger automation
/// automatically — no Info.plist registration needed.
struct LedgerAppShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: ApplePayCaptureIntent(),
            phrases: ["Registrar compra en \(.applicationName)"],
            shortTitle: "Registrar compra",
            systemImageName: "creditcard"
        )
    }
}

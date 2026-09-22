/**
 * Keep the file a person is fixing in THIS browser (IndexedDB) while they
 * step away to confirm their email or buy credits, so coming back — the
 * Stripe return, a reload, the verification link opened in the same tab —
 * continues with the same file instead of asking for it again.
 *
 * Local only: the bytes never leave the browser from here, they expire after
 * a few hours, and they are cleared as soon as the fix is done. Every call
 * fails soft (private windows and blocked storage just mean no resume).
 */
import { Platform } from "react-native";

const DB_NAME = "508-fixer";
const STORE = "pending";
const KEY = "file";
const MAX_BYTES = 100 * 1024 * 1024;
const MAX_AGE_MS = 6 * 60 * 60 * 1000;

export type PendingReason = "verify" | "buy";

export interface PendingFile {
  file: File;
  reason: PendingReason;
  savedAt: number;
}

function openDb(): Promise<IDBDatabase | null> {
  if (Platform.OS !== "web" || typeof indexedDB === "undefined") return Promise.resolve(null);
  return new Promise((resolve) => {
    try {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => {
        try {
          req.result.createObjectStore(STORE);
        } catch {
          /* already there */
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => resolve(null);
      req.onblocked = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

function run<T>(mode: IDBTransactionMode, op: (store: IDBObjectStore) => IDBRequest<T> | null): Promise<T | null> {
  return openDb().then(
    (db) =>
      new Promise<T | null>((resolve) => {
        if (!db) return resolve(null);
        try {
          const tx = db.transaction(STORE, mode);
          const req = op(tx.objectStore(STORE));
          let value: T | null = null;
          if (req) req.onsuccess = () => (value = req.result ?? null);
          tx.oncomplete = () => {
            db.close();
            resolve(value);
          };
          tx.onerror = () => {
            db.close();
            resolve(null);
          };
          tx.onabort = () => {
            db.close();
            resolve(null);
          };
        } catch {
          try {
            db.close();
          } catch {
            /* ignore */
          }
          resolve(null);
        }
      }),
  );
}

export async function savePendingFile(file: File, reason: PendingReason): Promise<boolean> {
  if (!file || file.size > MAX_BYTES) return false;
  const record = {
    blob: file as Blob,
    name: file.name,
    type: file.type,
    lastModified: file.lastModified,
    reason,
    savedAt: Date.now(),
  };
  const ok = await run("readwrite", (s) => s.put(record, KEY));
  return ok !== null;
}

export async function loadPendingFile(): Promise<PendingFile | null> {
  const rec: any = await run("readonly", (s) => s.get(KEY));
  if (!rec || !(rec.blob instanceof Blob) || typeof rec.name !== "string") return null;
  if (typeof rec.savedAt !== "number" || Date.now() - rec.savedAt > MAX_AGE_MS) {
    void clearPendingFile();
    return null;
  }
  try {
    const file = new File([rec.blob], rec.name, { type: rec.type || "", lastModified: rec.lastModified || Date.now() });
    return { file, reason: rec.reason === "buy" ? "buy" : "verify", savedAt: rec.savedAt };
  } catch {
    return null;
  }
}

export async function clearPendingFile(): Promise<void> {
  await run("readwrite", (s) => s.delete(KEY));
}

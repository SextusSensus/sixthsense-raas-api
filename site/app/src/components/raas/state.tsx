import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  api,
  friendly,
  inFrame,
  signIn as walletSignIn,
  signOut as walletSignOut,
  type Booking,
  type Config,
  type Me,
  type Slot,
} from "@/lib/raas";

import "./raas.css";

interface RaasState {
  config: Config | null;
  me: Me;
  slots: Slot[];
  bookings: Booking[];
  loaded: boolean;
  loadError: string | null;
  payNow: string | null;
  setPayNow: (id: string | null) => void;
  signIn: () => Promise<boolean>;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
  notify: (message: string, isError?: boolean) => void;
}

const RaasContext = createContext<RaasState | null>(null);

export function useRaas(): RaasState {
  const value = useContext(RaasContext);
  if (!value) throw new Error("useRaas must be used inside RaasProvider");
  return value;
}

export function RaasProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<Config | null>(null);
  const [me, setMe] = useState<Me>({ wallet: null, admin: false });
  const [slots, setSlots] = useState<Slot[]>([]);
  const [bookings, setBookings] = useState<Booking[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [payNow, setPayNow] = useState<string | null>(null);
  const [toast, setToast] = useState<{ message: string; isError: boolean } | null>(null);
  const [framed, setFramed] = useState(false);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const notify = useCallback((message: string, isError = false) => {
    setToast({ message, isError });
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), isError ? 7000 : 3500);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [cfg, sl, who] = await Promise.all([
        api<Config>("/api/config"),
        api<Slot[]>("/api/slots"),
        api<Me>("/api/me"),
      ]);
      setConfig(cfg.data);
      setSlots(sl.data);
      setMe(who.data);
      setBookings(who.data.wallet ? (await api<Booking[]>("/api/bookings")).data : []);
      setLoadError(null);
    } catch (e) {
      setLoadError(friendly(e));
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    setFramed(inFrame());
    void refresh();
  }, [refresh]);

  // Keep session status live (operator starts the session, results land) while signed in.
  useEffect(() => {
    if (!me.wallet) return;
    const timer = setInterval(() => void refresh(), 20000);
    return () => clearInterval(timer);
  }, [me.wallet, refresh]);

  const signIn = useCallback(async () => {
    if (!config) {
      notify(loadError ?? "The booking service is starting. Try again in a moment.", true);
      return false;
    }
    try {
      setMe(await walletSignIn());
      await refresh();
      notify("Wallet connected.");
      return true;
    } catch (e) {
      notify(friendly(e), true);
      return false;
    }
  }, [config, loadError, notify, refresh]);

  const signOut = useCallback(async () => {
    await walletSignOut();
    setMe({ wallet: null, admin: false });
    setBookings([]);
  }, []);

  const value = useMemo(
    () => ({ config, me, slots, bookings, loaded, loadError, payNow, setPayNow, signIn, signOut, refresh, notify }),
    [config, me, slots, bookings, loaded, loadError, payNow, signIn, signOut, refresh, notify],
  );

  return (
    <RaasContext.Provider value={value}>
      {framed || (loaded && loadError) ? (
        <div className="rx-banner" role="status">
          {framed ? (
            <span>
              Wallets cannot connect inside this preview.{" "}
              <a href="/" rel="noreferrer" target="_blank">
                Open the site in a new tab
              </a>
              .
            </span>
          ) : (
            <span>Booking is offline right now. You can look around; booking opens as soon as the service is back.</span>
          )}
        </div>
      ) : null}
      {children}
      {toast ? (
        <div className="rx-toast" data-error={toast.isError ? "" : undefined} role={toast.isError ? "alert" : "status"}>
          {toast.message}
        </div>
      ) : null}
    </RaasContext.Provider>
  );
}

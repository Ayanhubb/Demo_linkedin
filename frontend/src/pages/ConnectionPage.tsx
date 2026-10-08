import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { ApiError, getLinkedInStatus, linkedInConnectUrl } from "../api";

export function ConnectionPage() {
  const [params] = useSearchParams();
  const [connected, setConnected] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const notice = params.get("linkedin");
  const oauthError = params.get("linkedin_error");

  useEffect(() => {
    let active = true;
    getLinkedInStatus()
      .then((body) => {
        if (active) {
          setConnected(body.connected);
        }
      })
      .catch((reason: unknown) => {
        if (active) {
          setError(reason instanceof ApiError ? reason.message : "Could not read connection status");
        }
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <section className="narrow">
      <h1>LinkedIn</h1>
      <p className="lede">Connect the profile that should receive your posts.</p>
      {notice === "connected" ? <p className="banner success">LinkedIn is connected.</p> : null}
      {oauthError ? <p className="banner error">LinkedIn connection failed ({oauthError}).</p> : null}
      {error ? <p className="banner error">{error}</p> : null}
      <article className="card connection-card">
        <p className={connected ? "status connected" : "status disconnected"}>
          {connected === null ? "Checking…" : connected ? "Connected" : "Not Connected"}
        </p>
        <a className="button" href={linkedInConnectUrl}>
          Connect LinkedIn
        </a>
      </article>
    </section>
  );
}

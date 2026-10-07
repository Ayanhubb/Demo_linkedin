import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, getDashboard, type Dashboard } from "../api";

export function DashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getDashboard()
      .then((body) => {
        if (active) {
          setData(body);
        }
      })
      .catch((reason: unknown) => {
        if (active) {
          setError(reason instanceof ApiError ? reason.message : "Could not load the dashboard");
        }
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <section>
      <h1>Dashboard</h1>
      <p className="lede">Connection status and how many posts are waiting, published, or failed.</p>
      {error ? <p className="banner error">{error}</p> : null}
      <div className="metrics">
        <article className="card">
          <p className="metric-label">LinkedIn</p>
          <p className={data?.linkedin_connected ? "status connected" : "status disconnected"}>
            {data ? (data.linkedin_connected ? "Connected" : "Not Connected") : "…"}
          </p>
          <Link to="/connection">Manage connection</Link>
        </article>
        <article className="card">
          <p className="metric-label">Scheduled</p>
          <p className="metric">{data?.scheduled_count ?? "—"}</p>
        </article>
        <article className="card">
          <p className="metric-label">Published</p>
          <p className="metric">{data?.published_count ?? "—"}</p>
        </article>
        <article className="card">
          <p className="metric-label">Failed</p>
          <p className="metric">{data?.failed_count ?? "—"}</p>
        </article>
      </div>
    </section>
  );
}

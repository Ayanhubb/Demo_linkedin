import { NavLink, Route, Routes } from "react-router-dom";

import { ConnectionPage } from "./pages/ConnectionPage";
import { DashboardPage } from "./pages/DashboardPage";
import { PostsPage } from "./pages/PostsPage";
import { SchedulePage } from "./pages/SchedulePage";

const links = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/connection", label: "LinkedIn", end: false },
  { to: "/schedule", label: "Schedule", end: false },
  { to: "/posts", label: "Posts", end: false },
];

export function App() {
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <strong>Post Scheduler</strong>
        </div>
        <nav>
          {links.map((link) => (
            <NavLink key={link.to} to={link.to} end={link.end}>
              {link.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/connection" element={<ConnectionPage />} />
          <Route path="/schedule" element={<SchedulePage />} />
          <Route path="/posts" element={<PostsPage />} />
        </Routes>
      </main>
    </div>
  );
}

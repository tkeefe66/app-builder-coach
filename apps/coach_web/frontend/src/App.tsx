import { NavLink, Route, Routes } from "react-router-dom";
import Login from "./pages/Login";
import Overview from "./pages/Overview";
import Capabilities from "./pages/Capabilities";
import Activity from "./pages/Activity";
import Adoption from "./pages/Adoption";
import Cost from "./pages/Cost";
import Goals from "./pages/Goals";

const NAV = [
  ["/", "Overview"], ["/capabilities", "Capabilities"],
  ["/activity", "Activity"], ["/adoption", "Adoption"],
  ["/cost", "Cost"], ["/goals", "Goals & Coach"],
] as const;

export default function App() {
  return (
    <div className="shell">
      <nav className="sidenav">
        <div className="brand">Build Coach</div>
        {NAV.map(([to, label]) => (
          <NavLink key={to} to={to} end={to === "/"}>{label}</NavLink>
        ))}
      </nav>
      <main>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<Overview />} />
          <Route path="/capabilities" element={<Capabilities />} />
          <Route path="/activity" element={<Activity />} />
          <Route path="/adoption" element={<Adoption />} />
          <Route path="/cost" element={<Cost />} />
          <Route path="/goals" element={<Goals />} />
        </Routes>
      </main>
    </div>
  );
}

import {useEffect,useRef,useState} from 'react';
import {NavLink,Routes,Route,Link,useNavigate} from 'react-router-dom';
import {ShieldCheck,LayoutDashboard,Database,FileSearch,PackageSearch,Network,TrendingUp,ListChecks,FlaskConical,Search,Fingerprint,PanelLeft,type LucideIcon} from 'lucide-react';
import {Radar,RadarBatch} from './pages/Radar';
import {Overview,Ingestion,Evaluation} from './pages/System';
import {Claims,ClaimDetail} from './pages/Claims';
import {NetworkPage,Forecast} from './pages/Intelligence';
import {Queue,CaseWorkspace} from './pages/Cases';
import {StreamSync} from './pages/LiveMonitor';
const groups:{label:string;items:[string,string,LucideIcon][]}[]=[
 {label:'Overview',items:[['/','Dashboard',LayoutDashboard],['/queue','SIU queue',ListChecks],['/claims','Claims',FileSearch],['/supplytrace','SupplyTrace',PackageSearch]]},
 {label:'Intelligence',items:[['/network','Nexus network',Network],['/radar','Member radar',Fingerprint],['/forecast','Future risk',TrendingUp]]},
 {label:'System',items:[['/ingestion','Data ingestion',Database],['/evaluation','Models & evaluation',FlaskConical]]},
];
export default function App(){
 const [collapsed,setCollapsed]=useState(false);const [q,setQ]=useState('');const search=useRef<HTMLInputElement>(null);const navigate=useNavigate();
 // "/" focuses the global search unless the user is already typing in a field.
 useEffect(()=>{const onKey=(e:KeyboardEvent)=>{const t=e.target as HTMLElement;if(e.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(t.tagName)){e.preventDefault();setCollapsed(false);search.current?.focus();}};window.addEventListener('keydown',onKey);return()=>window.removeEventListener('keydown',onKey);},[]);
 return <div className={'app-shell'+(collapsed?' collapsed':'')}>
  <aside className="sidebar">
   <div className="brand-row"><Link to="/" className="brand"><span className="brand-mark"><ShieldCheck size={18}/></span><span><strong>ClaimShield</strong><small>Claims intelligence</small></span></Link><button className="icon-btn" onClick={()=>setCollapsed(!collapsed)} aria-label="Toggle sidebar"><PanelLeft size={17}/></button></div>
   <form className="side-search" onSubmit={e=>{e.preventDefault();navigate('/claims?q='+encodeURIComponent(q.trim()));}}><Search size={15}/><input ref={search} aria-label="Global search" placeholder="Search claims…" value={q} onChange={e=>setQ(e.target.value)}/><kbd>/</kbd></form>
   {groups.map(g=><div className="nav-group" key={g.label}><div className="nav-label">{g.label}</div><nav>{g.items.map(([path,label,Icon])=><NavLink key={path} to={path} end={path==='/'} title={label}><Icon size={17}/><span>{label}</span></NavLink>)}</nav></div>)}
   <div className="sidebar-user"><span className="avatar">DA</span><div><strong>Demo Analyst</strong><small>Team CIPHER</small></div></div>
  </aside>
  <main><div className="page"><StreamSync/><Routes><Route path="/" element={<Overview/>}/><Route path="/ingestion" element={<Ingestion/>}/><Route path="/claims" element={<Claims/>}/><Route path="/claims/:id" element={<ClaimDetail/>}/><Route path="/supplytrace" element={<Claims supply/>}/><Route path="/supplytrace/:id" element={<ClaimDetail supply/>}/><Route path="/network" element={<NetworkPage/>}/><Route path="/forecast" element={<Forecast/>}/><Route path="/radar" element={<Radar/>}/><Route path="/radar/:providerId" element={<RadarBatch/>}/><Route path="/queue" element={<Queue/>}/><Route path="/cases/:id" element={<CaseWorkspace/>}/><Route path="/evaluation" element={<Evaluation/>}/><Route path="*" element={<div className="empty">Page not found. <Link to="/">Back to dashboard</Link></div>}/></Routes></div></main>
 </div>;
}

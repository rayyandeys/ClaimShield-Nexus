import React from 'react';
import ReactDOM from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {BrowserRouter} from 'react-router-dom';
import App from './App';
import './styles.css';
import './theme.css';
const client=new QueryClient({defaultOptions:{queries:{staleTime:10000,refetchOnWindowFocus:false}}});
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><QueryClientProvider client={client}><BrowserRouter><App/></BrowserRouter></QueryClientProvider></React.StrictMode>);

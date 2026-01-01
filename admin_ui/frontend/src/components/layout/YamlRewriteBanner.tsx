import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { AlertCircle } from 'lucide-react';
import { Link, useLocation } from 'react-router-dom';
import { detectYamlFeatures } from '../../utils/yamlFeatures';

const YamlRewriteBanner = () => {
    const [show, setShow] = useState(false);
    const location = useLocation();

    useEffect(() => {
        const run = async () => {
            try {
                const res = await axios.get('/api/config/yaml');
                const content = res.data?.content || '';
                const flags = detectYamlFeatures(content);
                setShow(flags.hasAnchors || flags.hasAliases || flags.hasMergeKeys);
            } catch {
                // Non-fatal: no banner if config cannot be fetched.
                setShow(false);
            }
        };
        run();
    }, []);

    if (!show) return null;
    if (location.pathname === '/yaml') return null;

    return (
        <div className="border-b border-yellow-500/30 bg-yellow-500/10 text-yellow-700 dark:text-yellow-400 px-6 py-2">
            <div className="max-w-6xl mx-auto flex items-center justify-between gap-3">
                <div className="flex items-center gap-2 text-sm">
                    <AlertCircle className="w-4 h-4" />
                    <span>
                        This configuration uses YAML anchors/merge keys. Saving from form pages will normalize YAML and may expand anchors or remove comments.
                    </span>
                </div>
                <Link
                    to="/yaml"
                    className="text-sm font-medium underline underline-offset-4 hover:text-yellow-800 dark:hover:text-yellow-300"
                >
                    Open Raw Configuration
                </Link>
            </div>
        </div>
    );
};

export default YamlRewriteBanner;


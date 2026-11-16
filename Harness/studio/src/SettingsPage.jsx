import React from 'react';
import ModelSettings from './ModelSettings';
import HomeAssistantSettings from './HomeAssistantSettings';

export default function SettingsPage({onConnectionChanged}){
 return <div className="workspace-settings"><header className="page-heading"><div><h1>设置</h1><p>连接模型服务与打印机，配置保存在当前工作空间。</p></div></header><ModelSettings/><HomeAssistantSettings onChanged={onConnectionChanged}/></div>;
}

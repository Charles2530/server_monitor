'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = path.join(__dirname, '../static/editor-appearance/extension/context.js');
function uri(path, scheme='file') { return {scheme,path,fsPath:path,toString(){return scheme+':'+path;}}; }
function fixture({selection=false,missing=false,fail=false,delayed=false}={}) {
  const calls=[],registered=new Map(),messages=[];let active=0,maxActive=0;
  const vscode={
    Uri:{file:p=>uri(p)},
    window:{activeTextEditor:{document:{uri:uri('/project/a.py')},selection:{isEmpty:!selection}},
      showWarningMessage:m=>messages.push(m),showErrorMessage:m=>messages.push(m)},
    commands:{registerCommand:(key,fn)=>{registered.set(key,fn);return{dispose(){}};},
      executeCommand:async(...args)=>{calls.push(args);if(fail)throw new Error('private failure');if(delayed){active++;maxActive=Math.max(maxActive,active);await new Promise(resolve=>setTimeout(resolve,5));active--;}}},
    extensions:{getExtension:()=>missing?undefined:{isActive:true}},
    workspace:{fs:{readFile:()=>{throw Error('must not read content');},readDirectory:()=>{throw Error('must not walk directories');}}}
  };
  const module={exports:{}};
  vm.runInNewContext(fs.readFileSync(source,'utf8'),{module,exports:module.exports,require:name=>{assert.equal(name,'vscode');return vscode;}});
  module.exports.activate({subscriptions:[]});
  return{calls,messages,maxActive:()=>maxActive,run:(name,...args)=>registered.get('ilab.codex.'+name)(...args),vscode};
}
test('single active file uses nonempty selection; no file contents are read',async()=>{
  const f=fixture({selection:true});await f.run('addReference',uri('/project/a.py'));
  assert.deepEqual(f.calls,[['chatgpt.addToThread']]);
});
test('another file never inherits the active selection',async()=>{
  const f=fixture({selection:true});await f.run('addReference',uri('/project/b.py'));
  assert.equal(f.calls[0][0],'chatgpt.addFileToThread');assert.equal(f.calls[0][1].fsPath,'/project/b.py');
});
test('folder forwards one path reference and never enumerates children',async()=>{
  const f=fixture();await f.run('addFolderReference',uri('/project/src'));
  assert.equal(f.calls.length,1);assert.equal(f.calls[0][1].fsPath,'/project/src');
});
test('Explorer multiselect deduplicates exact paths and forwards each URI',async()=>{
  const f=fixture({selection:true}), a=uri('/project/a.py'), b=uri('/project/中文 space.py');
  await f.run('addReference',a,[a,b,a]);
  assert.equal(f.calls.length,2);assert.ok(f.calls.every(c=>c[0]==='chatgpt.addFileToThread'));
  assert.deepEqual(f.calls.map(c=>c[1].fsPath),[a.fsPath,b.fsPath]);
});
test('Explorer multiselect dispatches independent references without serial delay',async()=>{
  const f=fixture({delayed:true}), a=uri('/project/a.py'), b=uri('/project/b.py');
  await f.run('addReference',a,[a,b]);
  assert.equal(f.calls.length,2);assert.ok(f.maxActive()>1);
});
test('stale Explorer selection never attaches unrelated entries',async()=>{
  const f=fixture();await f.run('addReference',uri('/project/right.py'),[uri('/project/old.py')]);
  assert.deepEqual(f.calls.map(c=>c[1].fsPath),['/project/right.py']);
});
test('explicit file reference bypasses selection; remote URI resolves inside workspace host',async()=>{
  const f=fixture({selection:true});await f.run('addFileReference',uri('/project/a.py','vscode-remote'));
  assert.equal(f.calls[0][0],'chatgpt.addFileToThread');assert.equal(f.calls[0][1].scheme,'file');
});
test('selection action refuses empty selection or unsupported documents',async()=>{
  const f=fixture();await f.run('addSelectionReference');assert.equal(f.calls.length,0);
  f.vscode.window.activeTextEditor.document.uri=uri('/a','untitled');
  f.vscode.window.activeTextEditor.selection.isEmpty=false;
  await f.run('addSelectionReference');assert.equal(f.calls.length,0);
});
test('missing extension and command failures are visible and never auto-submit',async()=>{
  const f=fixture({missing:true});await f.run('addReference');assert.equal(f.calls.length,0);assert.equal(f.messages.length,1);
  const g=fixture({fail:true});await g.run('addReference');assert.equal(g.messages.length,1);
  assert.ok(!g.messages[0].includes('private failure'));
});
test('unsupported resource never becomes the active editor file',async()=>{
  const f=fixture();await f.run('addReference',uri('/a','https'));assert.equal(f.calls.length,0);
});

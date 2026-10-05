"""Soft-keyboard DOM traces run against the app's real xterm and outbound writes."""
import asyncio

# Composed events model WebView input. DOM updates are explicit because synthetic
# beforeinput/input events do not perform a browser's default textarea edit.
TRACE = r'''async ({scenario, text}) => {
 const a=window.testTerm.textarea;
 a.value='';a.focus();
 const key=(type,code,key='Unidentified')=>a.dispatchEvent(new KeyboardEvent(type,{key,keyCode:code,which:code,bubbles:true,cancelable:true,composed:true}));
 const edit=(data,type='insertText',composing=false)=>{
   if (!a.dispatchEvent(new InputEvent('beforeinput',{data,inputType:type,isComposing:composing,bubbles:true,cancelable:true,composed:true}))) return;
   if(type.startsWith('insert'))a.value+=data||'\n';
   a.dispatchEvent(new InputEvent('input',{data,inputType:type,isComposing:composing,bubbles:true,composed:true}));
 };
 const composition=(type,data)=>a.dispatchEvent(new CompositionEvent(type,{data,bubbles:true,composed:true}));
 const tick=()=>new Promise(r=>setTimeout(r,20));
 if(scenario==='229-input-keyup'){key('keydown',229);edit(text);key('keyup',229);}
 if(scenario==='229-keyup-input'){key('keydown',229);key('keyup',229);edit(text);}
 if(scenario==='input-only')edit(text);
 if(scenario.startsWith('mixed-')){
   if(scenario.includes('composition')){
     composition('compositionstart','');composition('compositionupdate','你好');
     edit('你好','insertCompositionText',true);await tick();
     composition('compositionend','你好');
   }
   const backwards=scenario.includes('backspace'), forwards=scenario.includes('delete');
   const code=backwards?8:forwards?46:13, name=backwards?'Backspace':forwards?'Delete':'Enter';
   const type=backwards?'deleteContentBackward':forwards?'deleteContentForward':'insertLineBreak';
   key('keydown',code,name);
   if(scenario.includes('keyup-first'))key('keyup',code,name);
   if(scenario.endsWith('input-only')){
     if(!backwards&&!forwards)a.value+='\n';
     a.dispatchEvent(new InputEvent('input',{inputType:type,bubbles:true,composed:true}));
   }else edit(null,type);
   if(!scenario.includes('keyup-first'))key('keyup',code,name);
 }
 if(scenario==='delete-input-only')a.dispatchEvent(new InputEvent('input',{inputType:'deleteContentBackward',bubbles:true,composed:true}));
 if(scenario==='enter-input-only')a.dispatchEvent(new InputEvent('input',{inputType:'insertLineBreak',bubbles:true,composed:true}));
 if(scenario==='delete')edit(null,'deleteContentBackward');
 if(scenario==='delete-forward')edit(null,'deleteContentForward');
 if(scenario==='delete-word')edit(null,'deleteWordBackward');
 if(scenario==='enter')edit(null,'insertLineBreak');
 if(scenario==='paragraph')edit(null,'insertParagraph');
 if(scenario==='paste')edit(text,'insertFromPaste');
 if(scenario==='composition-end-input' || scenario==='composition-229'){
   if(scenario==='composition-229')key('keydown',229);
   composition('compositionstart','');composition('compositionupdate',text);
   edit(text,'insertCompositionText',true);await tick();
   composition('compositionend',text);
   // The commit input describes the existing text, it does not insert it again.
   a.dispatchEvent(new InputEvent('input',{data:text,inputType:'insertText',bubbles:true,composed:true}));
   if(scenario==='composition-229')key('keyup',229);
 }
 if(scenario==='composition-cancel' || scenario==='composition-edit' || scenario==='composition-enter' || scenario==='composition-input-enter' || scenario==='composition-input-delete'){
   composition('compositionstart','');composition('compositionupdate','你好');
   edit('你好','insertCompositionText',true);await tick();
   if(scenario==='composition-cancel')a.value='';
   if(scenario==='composition-edit'){
     a.value='你';a.dispatchEvent(new InputEvent('input',{inputType:'deleteContentBackward',isComposing:true,bubbles:true,composed:true}));
   }
   composition('compositionend',a.value);
   if(scenario==='composition-enter')edit(null,'insertLineBreak');
   if(scenario==='composition-input-enter' || scenario==='composition-input-delete'){
     a.value=scenario==='composition-input-enter'?a.value+'\n':a.value.slice(0,-1);
     a.dispatchEvent(new InputEvent('input',{inputType:scenario==='composition-input-enter'?'insertLineBreak':'deleteContentBackward',bubbles:true,composed:true}));
   }
 }
 await tick();
}'''

async def check_mobile_input(page, inputs):
    cases = [
        ('mixed-composition-enter-input-only',None,'你好\r'),
        ('mixed-composition-backspace-input-only',None,'你好\x7f'),
        ('mixed-composition-enter',None,'你好\r'),
        ('mixed-composition-backspace',None,'你好\x7f'),
        ('mixed-enter',None,'\r'), ('mixed-enter-keyup-first',None,'\r'),
        ('mixed-enter-input-only',None,'\r'), ('mixed-enter-keyup-first-input-only',None,'\r'),
        ('mixed-backspace',None,'\x7f'), ('mixed-backspace-input-only',None,'\x7f'),
        ('mixed-delete',None,'\x1b[3~'), ('mixed-delete-input-only',None,'\x1b[3~'),
        ('229-keyup-input','a','a'), ('229-input-keyup','b','b'),
        ('input-only','c','c'), ('composition-end-input','a','a'),
        ('composition-229','你','你'), ('composition-end-input','好','好'),
        ('input-only','😀','😀'), ('229-keyup-input','12_./','12_./'),
        ('delete',None,'\x7f'), ('delete-input-only',None,'\x7f'), ('enter-input-only',None,'\r'), ('composition-cancel',None,''), ('composition-edit',None,'你'), ('composition-enter',None,'你好\r'), ('composition-input-enter',None,'你好\r'), ('composition-input-delete',None,'你好\x7f'), ('delete-forward',None,'\x1b[3~'),
        ('delete-word',None,'\x17'), ('enter',None,'\r'), ('paragraph',None,'\r'),
        ('paste','hello\nworld','hello\rworld'),
    ]
    for scenario, text, expected in cases:
        before=len(inputs)
        await page.evaluate(TRACE,{'scenario':scenario,'text':text})
        await asyncio.sleep(.06)
        actual=''.join(x['data'] for x in inputs[before:])
        assert actual==expected,(scenario,repr(expected),repr(actual))
    # Consecutive real Enters are distinct even immediately after a mixed event.
    before=len(inputs)
    for scenario in ('mixed-enter','enter-input-only','mixed-enter-input-only','enter'):
        await page.evaluate(TRACE,{'scenario':scenario,'text':None})
    await asyncio.sleep(.08)
    assert ''.join(x['data'] for x in inputs[before:])=='\r'*4
    # Genuine repeat characters and repeated presses must never be deduplicated.
    before=len(inputs)
    for _ in range(3):
        await page.evaluate(TRACE,{'scenario':'229-keyup-input','text':'a'})
    await page.locator('.xterm-helper-textarea').press_sequentially('bookkeeper')
    await page.locator('.xterm-helper-textarea').press('Backspace')
    await page.locator('.xterm-helper-textarea').press('Enter')
    await asyncio.sleep(.08)
    assert ''.join(x['data'] for x in inputs[before:])=='aaabookkeeper\x7f\r'
    # Paste honors the active terminal's bracketed-paste protocol.
    await page.evaluate("new Promise(r=>testTerm.write('\\x1b[?2004h',r))")
    before=len(inputs)
    await page.evaluate(TRACE,{'scenario':'paste','text':'one\ntwo'})
    await asyncio.sleep(.06)
    assert ''.join(x['data'] for x in inputs[before:])=='\x1b[200~one\rtwo\x1b[201~'
    await page.evaluate("new Promise(r=>testTerm.write('\\x1b[?2004l',r))")
    print('Mobile input traces passed: Android229 ordering, composition commits, repeated letters, Unicode, deletion, Enter and bracketed paste.',flush=True)

async def check_mobile_tools(page, inputs):
    # Sliding the narrow shortcut strip must scroll without sending the touched key.
    strip=await page.locator('.terminal-keyboard').bounding_box()
    cdp=await page.context.new_cdp_session(page)
    # Mobile emulation can extend below Chrome's native headless content view.
    # Keep the host surface large enough for real touch gestures; CSS viewport stays unchanged.
    native_window=await cdp.send('Browser.getWindowForTarget')
    await cdp.send('Browser.setWindowBounds',{'windowId':native_window['windowId'],'bounds':{'width':1200,'height':1000}})
    before=len(inputs)
    start=strip['x']+strip['width']-15; y=strip['y']+strip['height']/2
    await cdp.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':start,'y':y}]})
    for delta in (30,60,90,120,150):
        await cdp.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':start-delta,'y':y}]})
        await asyncio.sleep(.02)
    await cdp.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
    await asyncio.sleep(.08)
    assert await page.locator('.terminal-keyboard').evaluate('(node)=>node.scrollLeft') > 0, await page.locator('.terminal-keyboard').evaluate('(node)=>({left:node.scrollLeft,width:node.clientWidth,scroll:node.scrollWidth,rect:node.getBoundingClientRect().toJSON()})')
    assert not ''.join(x['data'] for x in inputs[before:]), 'Swipe must not send a shortcut'
    await cdp.detach()
    before=len(inputs)
    await page.locator('[data-terminal-key=ctrl]').tap()
    await page.locator('[data-terminal-key=escape]').tap()
    await page.locator('.xterm-helper-textarea').press('c')
    await asyncio.sleep(.08)
    assert ''.join(x['data'] for x in inputs[before:])=='\x1bc', 'Shortcut must consume one-shot Ctrl'
    await page.evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:{readText:async()=> 'q',writeText:async text=>{window.copiedText=text;}}})")
    await page.locator('[data-terminal-key=ctrl]').tap()
    before=len(inputs)
    await page.locator('#terminal-more').tap()
    await page.locator('#terminal-paste').tap()
    await asyncio.sleep(.08)
    assert ''.join(x['data'] for x in inputs[before:])=='q', 'Paste must stay literal even after Ctrl'
    assert not await page.locator('#terminal-menu').is_visible(), 'Successful paste should close tools'
    assert await page.locator('.xterm-helper-textarea').evaluate('(node)=>node===document.activeElement')
    # Selecting output and opening tools must preserve the exact selection.
    await page.evaluate('testTerm.select(0,0,5)')
    expected=await page.evaluate('testTerm.getSelection()')
    assert expected
    await page.locator('#terminal-more').tap()
    await page.locator('#terminal-copy').tap()
    assert await page.evaluate('window.copiedText')==expected
    await page.locator('#terminal-menu-close').tap()
    await page.locator('#terminal-mobile-keyboard').tap()
    print('Mobile tools passed: one-shot modifiers, literal clipboard paste, restored focus and exact copy.',flush=True)

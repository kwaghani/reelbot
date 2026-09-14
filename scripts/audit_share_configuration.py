"""Read-only audit of generated native share configuration and source copies."""
import json, plistlib, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'app'
GROUP = 'group.com.krishwaghani.reelbot'

def audit(app):
    ios = app / 'ios'
    project = json.loads(subprocess.check_output(['plutil', '-convert', 'json', '-o', '-', str(ios/'ReelBot.xcodeproj/project.pbxproj')]))
    objects = project['objects']; root = objects[project['rootObject']]
    result = {'workspace': str(ios/'ReelBot.xcworkspace'), 'targets': {}, 'groups': {}}
    for name in ['ReelBot', 'ReelBotShareExtension']:
        identifier, target = next((key, value) for key, value in objects.items() if value.get('isa') == 'PBXNativeTarget' and value['name'] == name)
        info = plistlib.loads((ios/name/'Info.plist').read_bytes())
        entitlements = plistlib.loads((ios/name/(name+'.entitlements')).read_bytes())
        for key in ['AppGroup', 'AppGroupIdentifier']:
            result['groups'][f'{name}/Info.plist:{key}'] = info[key]; assert info[key] == GROUP
        result['groups'][f'{name}/{name}.entitlements'] = entitlements['com.apple.security.application-groups']
        assert entitlements['com.apple.security.application-groups'] == [GROUP]
        capability = root['attributes']['TargetAttributes'][identifier]['SystemCapabilities']['com.apple.ApplicationGroups.iOS']['enabled']
        assert int(capability) == 1
        configurations = []
        for item in objects[target['buildConfigurationList']]['buildConfigurations']:
            cfg = objects[item]; settings = cfg['buildSettings']
            expected = 'com.krishwaghani.reelbot' + ('.ShareExtension' if name.endswith('Extension') else '')
            assert settings['PRODUCT_BUNDLE_IDENTIFIER'] == expected
            assert str(settings['IPHONEOS_DEPLOYMENT_TARGET']) == '15.1'
            assert settings['CODE_SIGN_ENTITLEMENTS'] == f'{name}/{name}.entitlements'
            configurations.append({'name':cfg['name'], 'bundle':expected, 'deployment':settings['IPHONEOS_DEPLOYMENT_TARGET'], 'build':settings['CURRENT_PROJECT_VERSION']})
        sources = []
        for phase_id in target['buildPhases']:
            phase = objects[phase_id]
            if phase['isa'] == 'PBXSourcesBuildPhase':
                sources += [objects[objects[item]['fileRef']]['path'] for item in phase['files']]
        result['targets'][name] = {'capability':True, 'configurations':configurations, 'sources':sources}
        if name.endswith('Extension'):
            rule = info['NSExtension']['NSExtensionAttributes']['NSExtensionActivationRule']
            assert rule == {'NSExtensionActivationSupportsText':True,'NSExtensionActivationSupportsWebURLWithMaxCount':1}
            assert all('ReelBotQueue.' not in source for source in sources)
            assert all(any(source.endswith(file) for source in sources) for file in ['QueueStore.swift','ShareDiagnostics.swift','SharedURL.swift','ShareExtensionViewController.swift'])
            result['activation_rule'] = rule
        else:
            embeds = [objects[p] for p in target['buildPhases'] if objects[p]['isa'] == 'PBXCopyFilesBuildPhase' and str(objects[p]['dstSubfolderSpec']) == '13']
            assert any(phase.get('name') == 'Embed App Extensions' and any(objects[objects[item]['fileRef']]['path'] == 'ReelBotShareExtension.appex' for item in phase['files']) for phase in embeds)
            result['embed_phase'] = 'Embed App Extensions'
    for folder, files in [('ReelBot',['QueueStore.swift','ShareDiagnostics.swift','ReelBotQueue.swift','ReelBotQueue.m']),('ReelBotShareExtension',['QueueStore.swift','ShareDiagnostics.swift','SharedURL.swift','ShareExtensionViewController.swift'])]:
        for file in files:
            assert (ios/folder/file).read_bytes() == (APP/'native'/file).read_bytes(), f'Stale generated source: {app}/{folder}/{file}'
    workspace = (ios/'ReelBot.xcworkspace/contents.xcworkspacedata').read_text()
    assert 'group:ReelBot.xcodeproj' in workspace
    assert 'ReelBot 2.xcodeproj' not in workspace
    return result

if __name__ == '__main__':
    import sys
    print(json.dumps(audit(Path(sys.argv[1]) if len(sys.argv)>1 else APP), indent=2))

const { withFinalizedMod } = require('expo/config-plugins');
const fs = require('fs');
const path = require('path');
const plist = require('@expo/plist').default;
module.exports = function withPersonalQueue(config) {
  return withFinalizedMod(config, ['ios', async mod => {
    const root = mod.modRequest.platformProjectRoot;
    const project = require('xcode').project(path.join(root, 'ReelBot.xcodeproj/project.pbxproj'));
    project.parseSync();
    const targets = project.pbxNativeTargetSection();
    const main = Object.keys(targets).find(key => targets[key]?.name?.replaceAll('"', '') === 'ReelBot');
    const extension = Object.keys(targets).find(key => targets[key]?.name?.replaceAll('"', '') === 'ReelBotShareExtension');
    if (!extension || !main) throw new Error('Expected app and share extension targets.');
    for (const [file, target, folder] of [['QueueStore.swift', main, 'ReelBot'], ['ReelBotQueue.swift', main, 'ReelBot'], ['ReelBotQueue.m', main, 'ReelBot'], ['QueueStore.swift', extension, 'ReelBotShareExtension']]) {
      const relative = `${folder}/${file}`;
      if (!project.hasFile(relative)) {
        const added = project.addSourceFile(relative, { target }, project.getFirstProject().firstProject.mainGroup);
        // The upstream extension uses a custom phase label; xcode's helper otherwise
        // falls back to the app's Sources phase. Assign by the target's actual phase.
        const sources = project.hash.project.objects.PBXSourcesBuildPhase;
        for (const phase of Object.values(sources)) if (phase.files) phase.files = phase.files.filter(entry => entry.value !== added.uuid);
        const destination = targets[target].buildPhases.find(phase => sources[phase.value]);
        sources[destination.value].files.push({ value: added.uuid, comment: `${file} in Sources` });
      }
    }
    const phases = project.hash.project.objects.PBXShellScriptBuildPhase;
    targets[extension].buildPhases = targets[extension].buildPhases.filter(phase => {
      const script = phases[phase.value];
      if (script && /Bundle React Native|Configure project|Start Packager/.test((script.name || '') + (script.shellScript || ''))) { delete phases[phase.value]; delete phases[phase.value + '_comment']; return false; }
      return true;
    });
    const configs = project.pbxXCBuildConfigurationSection();
    const lists = project.hash.project.objects.XCConfigurationList;
    for (const entry of lists[targets[extension].buildConfigurationList].buildConfigurations) {
      const settings = configs[entry.value].buildSettings;
      settings.APPLICATION_EXTENSION_API_ONLY = 'YES';
      settings.SWIFT_VERSION = '5.0';
      settings.OTHER_LDFLAGS = ['"$(inherited)"'];
    }
    fs.writeFileSync(path.join(root, 'ReelBot.xcodeproj/project.pbxproj'), project.writeSync());
    const native = path.join(mod.modRequest.projectRoot, 'native');
    for (const [folder, files] of [['ReelBot', ['QueueStore.swift', 'ReelBotQueue.swift', 'ReelBotQueue.m']], ['ReelBotShareExtension', ['QueueStore.swift', 'ShareExtensionViewController.swift']]]) {
      fs.mkdirSync(path.join(root, folder), { recursive: true });
      for (const file of files) fs.copyFileSync(path.join(native, file), path.join(root, folder, file));
    }
    const podfile = path.join(root, 'Podfile');
    const source = fs.readFileSync(podfile, 'utf8');
    const index = source.indexOf("target 'ReelBotShareExtension' do");
    if (index < 0) throw new Error('Expected generated extension Podfile target.');
    fs.writeFileSync(podfile, source.slice(0, index) + "# Queue-only extension uses Foundation/UIKit and no Pods.\ntarget 'ReelBotShareExtension' do\nend\n");
    const infoPath = path.join(root, 'ReelBotShareExtension', 'Info.plist');
    const info = plist.parse(fs.readFileSync(infoPath, 'utf8'));
    info.AppGroupIdentifier = config.extra.appGroupIdentifier;
    fs.writeFileSync(infoPath, plist.build(info));
    return mod;
  }]);
};

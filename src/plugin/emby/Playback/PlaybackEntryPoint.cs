#nullable enable

using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using System.Threading.Tasks;
using MediaBrowser.Controller;
using MediaBrowser.Controller.Library;
using MediaBrowser.Controller.Plugins;
using MediaBrowser.Model.Logging;
using MediaBrowser.Model.Services;

namespace Emby.Plugin.BlackBarrHelper.Playback
{
    public class PlaybackEntryPoint : IServerEntryPoint
    {
        public static PlaybackEntryPoint? Instance { get; private set; }

        public ILibraryManager LibraryManager { get; }
        public ILogManager LogManager { get; }
        public ILogger Logger { get; }
        public IServerApplicationHost AppHost { get; }

        public PlaybackEntryPoint(ILibraryManager libraryManager, ILogManager logManager, IServerApplicationHost appHost)
        {
            LibraryManager = libraryManager;
            LogManager = logManager;
            Logger = logManager.GetLogger("BlackBarrHelper");
            AppHost = appHost;
            Instance = this;
        }

        public void Run()
        {
            Logger.Info("[BlackBarr Helper] PlaybackEntryPoint Run() starting background monitor...");
            Task.Run(async () =>
            {
                try
                {
                    int attempts = 0;
                    while (attempts < 60)
                    {
                        attempts++;
                        if (AppHost.IsStartupComplete)
                        {
                            Logger.Info("[BlackBarr Helper] Startup complete detected on attempt {0}. Registering filters...", attempts);
                            await Task.Delay(500).ConfigureAwait(false);
                            if (RegisterPlaybackInfoFilter())
                            {
                                break;
                            }
                        }
                        await Task.Delay(500).ConfigureAwait(false);
                    }
                }
                catch (Exception ex)
                {
                    Logger.Error("[BlackBarr Helper] Error in background monitor task: {0}", ex);
                }
            });
        }

        private bool RegisterPlaybackInfoFilter()
        {
            try
            {
                object? serviceController = null;
                var httpServerProp = AppHost.GetType().GetProperty("HttpServer", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                var httpServer = httpServerProp?.GetValue(AppHost);
                if (httpServer != null)
                {
                    var scProp = httpServer.GetType().GetProperty("ServiceController", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                    serviceController = scProp?.GetValue(httpServer);
                }

                if (serviceController == null)
                {
                    var scProp = AppHost.GetType().GetProperty("ServiceController", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                    serviceController = scProp?.GetValue(AppHost);
                }

                if (serviceController == null)
                {
                    Logger.Warn("[BlackBarr Helper] ServiceController is null on AppHost and HttpServer");
                    return false;
                }

                IDictionary? restPathMap = null;
                var restPathMapProp = serviceController.GetType().GetProperty("RestPathMap", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                restPathMap = restPathMapProp?.GetValue(serviceController) as IDictionary;

                if (restPathMap == null)
                {
                    var restPathMapField = serviceController.GetType().GetField("RestPathMap", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                    restPathMap = restPathMapField?.GetValue(serviceController) as IDictionary;
                }

                if (restPathMap == null || restPathMap.Count == 0)
                {
                    Logger.Warn("[BlackBarr Helper] RestPathMap is null or empty on ServiceController");
                    return false;
                }

                var filter = new PlaybackInfoFilter(Logger);
                int injectedCount = 0;

                foreach (DictionaryEntry entry in restPathMap)
                {
                    var pathList = entry.Value as IEnumerable;
                    if (pathList == null) continue;

                    foreach (var restPath in pathList)
                    {
                        if (restPath == null) continue;

                        var reqTypeProp = restPath.GetType().GetProperty("RequestType");
                        var reqType = reqTypeProp?.GetValue(restPath) as Type;

                        if (reqType != null && (reqType.Name.Contains("PlaybackInfo") || reqType.Name.Contains("PostedPlaybackInfo")))
                        {
                            var filtersProp = restPath.GetType().GetProperty("RequestFilters");
                            if (filtersProp != null)
                            {
                                var currentFilters = filtersProp.GetValue(restPath) as IHasRequestFilter[];
                                var newFilters = new List<IHasRequestFilter>(currentFilters ?? Array.Empty<IHasRequestFilter>());
                                bool exists = false;
                                foreach (var f in newFilters)
                                {
                                    if (f is PlaybackInfoFilter)
                                    {
                                        exists = true;
                                        break;
                                    }
                                }
                                if (!exists)
                                {
                                    newFilters.Add(filter);
                                    filtersProp.SetValue(restPath, newFilters.ToArray());
                                    injectedCount++;
                                }
                            }
                        }
                    }
                }

                if (injectedCount > 0)
                {
                    Logger.Info("[BlackBarr Helper] Successfully injected PlaybackInfoFilter into {0} routes!", injectedCount);
                    return true;
                }
                else
                {
                    Logger.Warn("[BlackBarr Helper] RestPathMap found but 0 PlaybackInfo routes matched");
                }
            }
            catch (Exception ex)
            {
                Logger.Error("[BlackBarr Helper] Error registering PlaybackInfoFilter: {0}", ex);
            }

            return false;
        }

        public void Dispose()
        {
            Instance = null;
        }
    }
}

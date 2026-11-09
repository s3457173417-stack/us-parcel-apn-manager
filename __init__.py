def classFactory(iface):
    from .plugin import ParcelAPNManagerPlugin
    return ParcelAPNManagerPlugin(iface)

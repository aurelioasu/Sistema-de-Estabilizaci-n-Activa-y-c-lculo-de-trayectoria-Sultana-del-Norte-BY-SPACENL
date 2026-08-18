from __future__ import annotations

import json

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class _MapBridge(QObject):
    location_selected = Signal(float, float)
    map_center_changed = Signal(float, float, float)

    @Slot(float, float)
    def select_launch_site(self, latitude: float, longitude: float) -> None:
        self.location_selected.emit(latitude, longitude)

    @Slot(float, float, float)
    def update_map_center(self, latitude: float, longitude: float, zoom: float) -> None:
        self.map_center_changed.emit(latitude, longitude, zoom)


class MapWidget(QWidget):
    """Interactive MapLibre/OpenStreetMap map with launch, landing, and flight path."""

    location_selected = Signal(float, float)
    map_center_changed = Signal(float, float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._web = None
        self._ready = False
        self._launch: tuple[float, float] | None = None
        self._flight: tuple[list[list[float]], list[float] | None] | None = None
        self._dispersion: tuple[float, float, tuple[float, float], tuple[float, float, float], str] | None = None
        try:
            from PySide6.QtWebChannel import QWebChannel
            from PySide6.QtWebEngineCore import QWebEngineSettings
            from PySide6.QtWebEngineWidgets import QWebEngineView

            self._web = QWebEngineView(self)
            self._web.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
            self._bridge = _MapBridge(self)
            self._bridge.location_selected.connect(self.location_selected)
            self._bridge.map_center_changed.connect(self.map_center_changed)
            channel = QWebChannel(self._web.page())
            channel.registerObject("bridge", self._bridge)
            self._web.page().setWebChannel(channel)
            self._web.loadFinished.connect(self._on_loaded)
            self._web.setHtml(self._html(), QUrl("https://sultana.local/"))
            layout.addWidget(self._web)
        except ImportError:
            self._fallback = QLabel("Mapa no disponible. Instale PySide6-WebEngine para usar el mapa interactivo.")
            self._fallback.setWordWrap(True)
            layout.addWidget(self._fallback)

    @staticmethod
    def _html() -> str:
        return """
<!doctype html><html><head><meta charset='utf-8'>
<link href='https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css' rel='stylesheet'>
<script src='qrc:///qtwebchannel/qwebchannel.js'></script>
<script src='https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js'></script>
<style>html,body,#map{width:100%;height:100%;margin:0;background:#172033}.maplibregl-ctrl-attrib{font-size:10px}#dispersion-legend{display:none;position:absolute;z-index:2;left:10px;bottom:24px;max-width:310px;padding:8px 10px;border-radius:5px;background:rgba(23,21,19,.88);color:#fff8f1;font:12px 'Segoe UI',sans-serif;box-shadow:0 1px 4px #000}.maplibregl-popup-content{padding:0;border-radius:6px}.dispersion-popup{width:245px;color:#1d2939;font:12px 'Segoe UI',sans-serif}.dispersion-popup__header{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:7px 9px;background:#f7f9fc;border-radius:6px 6px 0 0}.dispersion-popup__title{font-weight:700}.dispersion-popup__toggle{min-width:25px;height:24px;border:0;border-radius:4px;background:#e4e9f1;color:#173457;cursor:pointer;font-size:16px;font-weight:700;line-height:20px}.dispersion-popup__body{padding:8px 9px;line-height:1.42;white-space:pre-line}.dispersion-popup.is-minimized{width:auto}.dispersion-popup.is-minimized .dispersion-popup__header{border-radius:6px}.dispersion-popup.is-minimized .dispersion-popup__body{display:none}</style>
</head><body><div id='map'><div id='dispersion-legend'></div></div><script>
let bridge=null, launchMarker=null, landingMarker=null, parachuteMarker=null, dispersionMarker=null, dispersionPopup=null;
const map = new maplibregl.Map({container:'map', center:[-100.311,25.681], zoom:17,
  style:{version:8, sources:{osm:{type:'raster',tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],tileSize:256,attribution:'© OpenStreetMap contributors'}},
         layers:[{id:'osm',type:'raster',source:'osm',minzoom:0,maxzoom:19}]}});
map.addControl(new maplibregl.NavigationControl(), 'top-right');
new QWebChannel(qt.webChannelTransport, c => { bridge=c.objects.bridge; });
map.on('click', e => { if (bridge) bridge.select_launch_site(e.lngLat.lat, e.lngLat.lng); });
map.on('moveend', () => { const c=map.getCenter(); if (bridge) bridge.update_map_center(c.lat, c.lng, map.getZoom()); });
function marker(color, point, current) { if (current) current.remove(); return new maplibregl.Marker({color}).setLngLat(point).addTo(map); }
function dispersionPopupContent(label) {
  const card=document.createElement('div'); card.className='dispersion-popup';
  const header=document.createElement('div'); header.className='dispersion-popup__header';
  const title=document.createElement('span'); title.className='dispersion-popup__title'; title.textContent='Analisis de dispersion';
  const toggle=document.createElement('button'); toggle.className='dispersion-popup__toggle'; toggle.type='button'; toggle.textContent='−'; toggle.title='Minimizar tarjeta'; toggle.setAttribute('aria-label','Minimizar tarjeta');
  const body=document.createElement('div'); body.className='dispersion-popup__body'; body.textContent=label;
  toggle.onclick=() => { const minimized=card.classList.toggle('is-minimized'); toggle.textContent=minimized?'+':'−'; toggle.title=minimized?'Ampliar tarjeta':'Minimizar tarjeta'; toggle.setAttribute('aria-label',toggle.title); };
  header.append(title,toggle); card.append(header,body); return card;
}
window.setLaunchSite = (lon,lat,name) => { launchMarker=marker('#00c7ff',[lon,lat],launchMarker); launchMarker.setPopup(new maplibregl.Popup().setText('Despegue: '+name)); map.flyTo({center:[lon,lat],zoom:17}); };
window.setFlight = (points, chutePoint) => {
  if (!points || !points.length) return;
  landingMarker=marker('#ff5c5c',points[points.length-1],landingMarker);
  landingMarker.setPopup(new maplibregl.Popup().setText('Aterrizaje simulado'));
  if (chutePoint) { parachuteMarker=marker('#ffae00',chutePoint,parachuteMarker); parachuteMarker.setPopup(new maplibregl.Popup().setText('Apertura de paracaidas')); }
  else if (parachuteMarker) { parachuteMarker.remove(); parachuteMarker=null; }
  const feature={type:'Feature',geometry:{type:'LineString',coordinates:points}};
  if(map.getSource('flight')) map.getSource('flight').setData(feature);
  else { map.addSource('flight',{type:'geojson',data:feature}); map.addLayer({id:'flight-line',type:'line',source:'flight',paint:{'line-color':'#ffbf4d','line-width':4}}); }
  const bounds=points.reduce((b,p)=>b.extend(p),new maplibregl.LngLatBounds(points[0],points[0])); map.fitBounds(bounds,{padding:40,maxZoom:14});
};
window.setDispersion = (originLat, originLon, centerEnu, ellipse, label) => {
  const earthRadius=6378137, radians=Math.PI/180, heading=ellipse[2]*radians, major=ellipse[0], minor=ellipse[1];
  const toLngLat=(east,north)=>[originLon + east/(earthRadius*Math.max(0.01,Math.cos(originLat*radians)))/radians, originLat + north/earthRadius/radians];
  const coordinates=[];
  for(let index=0; index<=72; index++) { const theta=index/72*2*Math.PI; const east=centerEnu[0]+major*Math.cos(theta)*Math.cos(heading)-minor*Math.sin(theta)*Math.sin(heading); const north=centerEnu[1]+major*Math.cos(theta)*Math.sin(heading)+minor*Math.sin(theta)*Math.cos(heading); coordinates.push(toLngLat(east,north)); }
  const feature={type:'Feature',geometry:{type:'Polygon',coordinates:[coordinates]}};
  if(map.getSource('dispersion')) map.getSource('dispersion').setData(feature);
  else { map.addSource('dispersion',{type:'geojson',data:feature}); map.addLayer({id:'dispersion-fill',type:'fill',source:'dispersion',paint:{'fill-color':'#9b5de5','fill-opacity':0.18}}); map.addLayer({id:'dispersion-outline',type:'line',source:'dispersion',paint:{'line-color':'#c77dff','line-width':3,'line-dasharray':[2,1]}}); }
  const center=toLngLat(centerEnu[0],centerEnu[1]); dispersionMarker=marker('#9b5de5',center,dispersionMarker); dispersionMarker.setPopup(new maplibregl.Popup().setText('Centro estimado de dispersión'));
  if(dispersionPopup) dispersionPopup.remove(); dispersionPopup=new maplibregl.Popup({closeButton:false,closeOnClick:false,offset:16}).setLngLat(center).setDOMContent(dispersionPopupContent(label)).addTo(map);
  const legend=document.getElementById('dispersion-legend'); legend.textContent='El área sombreada contiene aproximadamente 95% de las corridas simuladas.'; legend.style.display='block';
  const bounds=coordinates.reduce((b,p)=>b.extend(p),new maplibregl.LngLatBounds(coordinates[0],coordinates[0])); map.fitBounds(bounds,{padding:55,maxZoom:14});
};
window.clearDispersion = () => {
  if(dispersionMarker) { dispersionMarker.remove(); dispersionMarker=null; }
  if(dispersionPopup) { dispersionPopup.remove(); dispersionPopup=null; }
  document.getElementById('dispersion-legend').style.display='none';
  for(const layer of ['dispersion-outline','dispersion-fill']) if(map.getLayer(layer)) map.removeLayer(layer);
  if(map.getSource('dispersion')) map.removeSource('dispersion');
};
</script></body></html>"""

    def _on_loaded(self, ok: bool) -> None:
        self._ready = ok
        if ok and self._launch:
            self.set_launch_site(*self._launch)
        if ok and self._flight:
            self.set_flight_path(*self._flight)
        if ok and self._dispersion:
            self.set_dispersion(*self._dispersion)

    def _javascript(self, expression: str) -> None:
        if self._web and self._ready:
            self._web.page().runJavaScript(expression)

    def set_launch_site(self, latitude: float, longitude: float, name: str = "sitio seleccionado") -> None:
        self._launch = (latitude, longitude)
        self._javascript(f"window.setLaunchSite({longitude:.8f}, {latitude:.8f}, {json.dumps(name)});")

    def set_flight_path(self, coordinates_lon_lat: list[list[float]], parachute_point_lon_lat: list[float] | None = None) -> None:
        self._flight = (coordinates_lon_lat, parachute_point_lon_lat)
        self._javascript(f"window.setFlight({json.dumps(coordinates_lon_lat)}, {json.dumps(parachute_point_lon_lat)});")

    def set_dispersion(
        self, origin_latitude: float, origin_longitude: float, center_enu_m: tuple[float, float],
        ellipse_95_m: tuple[float, float, float], label: str,
    ) -> None:
        self._dispersion = (origin_latitude, origin_longitude, center_enu_m, ellipse_95_m, label)
        self._javascript(
            "window.setDispersion("
            f"{origin_latitude:.8f}, {origin_longitude:.8f}, {json.dumps(center_enu_m)}, {json.dumps(ellipse_95_m)}, {json.dumps(label)});"
        )

    def clear_dispersion(self) -> None:
        self._dispersion = None
        self._javascript("window.clearDispersion();")

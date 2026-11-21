// ============================================================================
// MikuLink.cs — côté Unity du pont MIKU-LINK (v1.1)
//
// Pose ce composant sur le GameObject qui porte le modèle riggé de Miku
// (GLB converti). Il :
//   1) se connecte au post MIKU (python miku_link, port 7310) ;
//   2) envoie `hello` ;
//   3) sonde le sol sous ses pieds (raycast) et envoie `world` 10x/seconde ;
//   4) reçoit `pose` (angles des 6 joints de jambes, degres) et les applique
//      aux os correspondants ;
//   5) relaie chat/gesture/emote vers des events C# (pour ton UI/son).
//
// Les IA amies se connectent au MEME post avec le MEME protocole : elles ont
// juste un autre nom d'agent. Rien à changer côté C#.
//
// JSON : parseur manuel minimal (Unity JsonUtility ne gère pas les listes
// libres) — aucune dépendance.
// ============================================================================
using System;
using System.Collections;
using System.Collections.Generic;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using UnityEngine;

public class MikuLink : MonoBehaviour
{
    [Header("Connexion au post")]
    public string host = "127.0.0.1";
    public int port = 7310;
    public string nomAgent = "MondeUnity";   // ex : "MIKU-01", "NOVA-01"

    [Header("Corps : os des jambes (ordre obligatoire)")]
    // 0-2 : jambe DROITE (hanche, genou, cheville) ; 3-5 : jambe GAUCHE
    public Transform[] jointsJambes = new Transform[6];

    [Header("Perception du sol")]
    public Transform sondeSol;            // peut rester null : on utilise le pivot
    public float largeurSondes = 0.6f;    // demi-largeur du profil de sol
    public int nbSondes = 7;
    public float distanceRaycast = 3f;

    [Header("Events pour ton jeu (UI, son, caméra...)")]
    public event Action<string, string> SurChat;       // (de, texte)
    public event Action<string, string> SurEmote;      // (de, émotion)
    public event Action<string, string> SurArrivee;    // (agent, perception)

    TcpClient _client;
    NetworkStream _flux;
    Thread _lireThread;
    readonly object _verrouSends = new object();
    volatile bool _vivant;
    Queue<string> _aAppliquer = new Queue<string>();  // messages reçus -> main thread

    [Serializable] class PoseMsg { public string type; public double[] q; public double lean; public bool chute; }

    void Start()
    {
        _vivant = true;
        StartCoroutine(AbstractReconnexion());   // (re)connexion auto en boucle
        StartCoroutine(EnvoyerMonde());
    }

    /// Nécessaire : si le post Python redémarre (ou n'a pas encore lancé),
    /// Unity se RECONNECTE tout seul toutes les 2 s au lieu de rester mort.
    IEnumerator AbstractReconnexion()
    {
        while (_vivant)
        {
            if (_flux == null)
            {
                try
                {
                    _client?.Close();
                }
                catch { }
                try
                {
                    Connecter();
                    Debug.Log("[MikuLink] post (re)connecté");
                }
                catch (Exception e)
                {
                    Debug.LogWarning("[MikuLink] post pas encore là (" + e.Message + "), nouvel essai dans 2 s");
                }
            }
            // la lecture est morte ? elle signale -> prochaine itération reconnecte
            if (_lireThread != null && !_lireThread.IsAlive)
            {
                try { _flux = null; } catch { }
            }
            _vivant = Application.isPlaying;
            yield return new WaitForSeconds(2f);
        }
    }

    void Connecter()
    {
        _client = new TcpClient();
        _client.Connect(host, port);
        _flux = _client.GetStream();
        EnvoiJson($"{{\"type\":\"hello\",\"proto\":\"miku-link/1\",\"agent\":\"{nomAgent}\",\"perception\":\"moteur jeu\"}}");
        _lireThread = new Thread(LireBoucle) { IsBackground = true };
        _lireThread.Start();
    }

    void OnApplicationQuit()
    {
        _vivant = false;
        try { _client?.Close(); } catch { }
    }

    // ---------------------------------------------------------------- envoi
    void EnvoiJson(string json)
    {
        byte[] b = Encoding.UTF8.GetBytes(json + "\n");
        lock (_verrouSends)
        {
            try { _flux.Write(b, 0, b.Length); }
            catch { _flux = null; }   // coupé -> la reconnexion auto reprendra
        }
    }

    IEnumerator EnvoyerMonde()
    {
        var att = new WaitForSeconds(0.1f);   // 10 Hz
        while (_vivant)
        {
            yield return att;
            if (_flux == null) continue;
            var origine = (sondeSol != null ? sondeSol : transform).position;
            var sb = new StringBuilder(256);
            sb.Append("{\"type\":\"world\",\"sol\":[");
            for (int i = 0; i < nbSondes; i++)
            {
                float t = nbSondes < 2 ? 0 : (float)i / (nbSondes - 1) * 2f - 1f;
                var o = origine + new Vector3(t * largeurSondes, 0.5f, 0);
                float h = 0f;
                if (Physics.Raycast(o, Vector3.down, out var hit, distanceRaycast))
                    h = o.y - hit.distance;                     // hauteur du sol (x local)
                if (i > 0) sb.Append(",");
                sb.Append($"[{(o.x - origine.x):F2},{h:F2}]");
            }
            sb.Append("]}");
            EnvoiJson(sb.ToString());
        }
    }

    // ------------------------------------------------------------- réception
    void LireBoucle()
    {
        var buf = new StringBuilder();
        var tampon = new byte[8192];
        try
        {
            while (_vivant)
            {
                int n = _flux.Read(tampon, 0, tampon.Length);
                if (n <= 0) break;
                buf.Append(Encoding.UTF8.GetString(tampon, 0, n));
                string s = buf.ToString();
                int nl;
                while ((nl = s.IndexOf('\n')) >= 0)
                {
                    string ligne = s.Substring(0, nl).Trim();
                    s = s.Substring(nl + 1);
                    if (ligne.Length > 0) lock (_aAppliquer) { _aAppliquer.Enqueue(ligne); }
                }
                buf.Clear(); buf.Append(s);
            }
        }
        catch { }
    }

    void Update()
    {
        lock (_aAppliquer)
        {
            while (_aAppliquer.Count > 0)
                TraiterMessage(_aAppliquer.Dequeue());
        }
    }

    void TraiterMessage(string ligne)
    {
        // events chat/emote/arrivee (recherche par sous-chaîne : suffisant ici)
        if (ligne.Contains("\"type\":\"chat\""))
        {
            var de = ExtraireChamp(ligne, "de"); var txt = ExtraireChamp(ligne, "texte");
            if (txt != null) SurChat?.Invoke(de, txt);
        }
        else if (ligne.Contains("\"type\":\"emote\""))
            SurEmote?.Invoke(ExtraireChamp(ligne, "de"), ExtraireChamp(ligne, "emotion"));
        else if (ligne.Contains("\"type\":\"arrivee\""))
        {
            var agent = ExtraireChamp(ligne, "agent");
            SurArrivee?.Invoke(agent, ExtraireChamp(ligne, "perception"));
            // l'annuaire vivant : un agent CONNU est salué différemment
            // (« re ! ») par qui veut l'entendre — la vie sociale a une
            // mémoire, c'est le post Python qui la porte.
            var connu = ExtraireChamp(ligne, "connu");
            if (connu == "True")
                SurEmote?.Invoke(agent, "revient (" + ExtraireChamp(ligne, "rencontres") + "e fois)");
        }
        else if (ligne.Contains("\"type\":\"pose\""))
        {
            var pose = JsonUtility.FromJson<PoseMsg>(ligne);
            if (pose != null && pose.q != null) AppliquerPose(pose.q);
        }
    }

    /// Applique la pose apprise : q[0..5] = hanche/genou/cheville R puis L, en radians.
    /// Convention physique MIKU : hanche + vers l'avant, genou = flexion (négatif).
    /// Ajuste les axes selon l'orientation de ton rig si besoin (multiplier par -1).
    void AppliquerPose(double[] q)
    {
        for (int i = 0; i < jointsJambes.Length && i < q.Length; i++)
        {
            if (jointsJambes[i] == null) continue;
            if (!float.IsFinite((float)q[i])) continue;   // NaN/Inf = on ignore
            var e = jointsJambes[i].localRotation.eulerAngles;
            float angle = (float)(q[i] * Mathf.Rad2Deg);
            // les(genoux : le moteur envoie une flexion négative -> plier le genou
            jointsJambes[i].localRotation = Quaternion.Euler(e.z, e.y, -angle); // axe X = pitch
        }
    }

    static string ExtraireChamp(string json, string champ)
    {
        string cle = $"\"{champ}\":";
        int i = json.IndexOf(cle, StringComparison.Ordinal);
        if (i < 0) return null;
        i += cle.Length;
        while (i < json.Length && json[i] == ' ') i++;
        if (i >= json.Length) return null;
        if (json[i] == '"')
        {
            int j = json.IndexOf('"', i + 1);
            return j < 0 ? null : json.Substring(i + 1, j - i - 1);
        }
        int k = i;
        while (k < json.Length && ",}]".IndexOf(json[k]) < 0) k++;
        return json.Substring(i, k - i).Trim();
    }
}

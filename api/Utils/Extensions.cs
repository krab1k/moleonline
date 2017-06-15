using Mole.API.Models;
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Threading.Tasks;

namespace Mole.API.Utils
{
    public static class Extensions
    {
        /// <summary>
        /// Determines if collection is null or empty
        /// </summary>
        /// <typeparam name="T"></typeparam>
        /// <param name="enumerable"></param>
        /// <returns></returns>
        public static bool IsNullOrEmpty<T>(this IEnumerable<T> enumerable)
        {
            return enumerable == null || !enumerable.Any();
        }

        /// <summary>
        /// 
        /// </summary>
        /// <param name="path"></param>
        /// <returns></returns>
        public static byte[] ZipDirectory(string path) {
            var temp = "temp.zip";

            ZipFile.CreateFromDirectory(path, temp);
            var bytes = File.ReadAllBytes(temp);
            File.Delete(temp);

            return bytes;
        }


        /// <summary>
        /// Compress entire direct to the 
        /// </summary>
        /// <param name="path"></param>
        public static void ZipDirectories(string path) {
            using (FileStream zipToOpen = new FileStream(Path.Combine(path, MoleApiFiles.Report), FileMode.Create))
            {
                using (ZipArchive archive = new ZipArchive(zipToOpen, ZipArchiveMode.Create))
                {
                    ZipArchiveEntry readmeEntry;
                    DirectoryInfo d = new DirectoryInfo(path);
                    foreach (var item in d.GetDirectories("*"))
                    {
                        FileInfo[] Files = item.GetFiles("*");
                        foreach (FileInfo file in Files)
                        {
                            readmeEntry = archive.CreateEntryFromFile(Path.Combine(path, item.Name, file.Name), Path.Combine(item.Name, file.Name));
                        }
                    }

                }
            }

        }


        /// <summary>
        /// Download gzipped file from a given url location and stores it in provided file
        /// </summary>
        /// <param name="url">Url to be hit</param>
        /// <param name="fileName">Destination</param>
        /// <returns></returns>
        public static string DownloadCompressedGz(string url, string fileName)
        {
            if (File.Exists(fileName)) File.Delete(fileName);

            try
            {
                using (WebClient c = new WebClient())
                {
                    using (Stream s = c.OpenRead(url))
                    {
                        using (GZipStream stream = new GZipStream(s, CompressionMode.Decompress))
                        {
                            ReadStream(fileName, stream);
                        }
                    }
                }
            }
            catch (WebException)
            {
                return $"Error downloading from: {url}";
            }
            return string.Empty;
        }


        public static void ReadStream(string fileName, Stream stream)
        {
            using (var outputStream = new FileStream(fileName, FileMode.Create))
            {
                const int size = 4096;
                byte[] buffer = new byte[size];
                using (MemoryStream memory = new MemoryStream())
                {
                    int count = 0;
                    do
                    {
                        count = stream.Read(buffer, 0, size);
                        if (count > 0)
                            outputStream.Write(buffer, 0, count);
                    }
                    while (count > 0);
                }
            }
        }
    }
}
